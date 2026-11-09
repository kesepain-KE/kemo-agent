"""HTTP answers and recovery for native questions in the original conversation."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from run.cards import CardConflictError, CardStore, IntegrationStore
from run.config import interactive_decisions_enabled, list_users, load_config
from run.tools.user_decisions import DecisionClosedError, DecisionError, DecisionStore
from web.errors import ConflictError, InvalidRequestError, NotFoundError
from events import RunEvent

if TYPE_CHECKING:
    from web.service import WebRunService


class DecisionServiceMixin:
    if TYPE_CHECKING:
        root: Path
        require_user = WebRunService.require_user
        require_source = WebRunService.require_source
        require_session_id = WebRunService.require_session_id
        _active_runs = WebRunService._active_runs
        _active_runs_lock = WebRunService._active_runs_lock
        event_source: Callable[..., Iterator[RunEvent]]

    def decisions(self, user, session_id, *, source="web"):
        name, identity = self.require_user(user), self.require_session_id(session_id)
        origin = self.require_source(source)
        enabled = interactive_decisions_enabled(load_config(name, self.root))
        store = DecisionStore(CardStore(self.root, name))
        store.refresh(origin, identity)
        state = store.store.session(origin, identity)
        return {"user": name, "source": origin, "session_id": identity,
                "enabled": enabled, "revision": state["last_seq"] if state else 0,
                "decisions": store.list(origin, identity) if enabled else []}

    def answer_decision(self, user, session_id, decision_id, *, source="web", option_id="", text=""):
        name, identity = self.require_user(user), self.require_session_id(session_id)
        origin = self.require_source(source)
        if not interactive_decisions_enabled(load_config(name, self.root)):
            raise ConflictError("该用户已关闭交互式决策")
        try:
            decision = DecisionStore(CardStore(self.root, name)).settle(
                origin, identity, decision_id, status="answered", option_id=option_id, text=text,
            )
        except KeyError:
            raise NotFoundError("当前会话中没有这个问题") from None
        except (DecisionClosedError, CardConflictError) as error:
            raise ConflictError(str(error)) from None
        except DecisionError as error:
            raise InvalidRequestError(str(error)) from None
        # A late HTTP submit may itself win the timeout transition, never
        # report that the user's answer was accepted in that case.
        if decision["status"] != "answered":
            raise ConflictError("该问题已超时或关闭，未接受此次回答")
        return {"decision": decision}

    def recover_decision_runs(self):
        """Called by one Web lifecycle worker; no request data comes from HTTP."""
        from web.services.chat_stream import ActiveRun
        from uuid import uuid4
        for user in list_users(self.root):
            store = CardStore(self.root, user)
            if not store.path.exists():
                continue
            with store.db() as db:
                rows = db.execute(
                    "SELECT i.*,s.source,s.session_id FROM card_integrations i "
                    "JOIN card_sessions s USING(session_key) WHERE i.response_closed=0 "
                    "AND s.lifecycle='open' AND s.generation=i.generation "
                    "AND s.source IN ('web','app') "
                    "AND json_type(i.record_json,'$.decision_checkpoint')='object'",
                ).fetchall()
                candidates = [dict(row) for row in rows if IntegrationStore._owner_lost(row)
                              and IntegrationStore._decision_resumable(db, row)]
            for row in candidates:
                checkpoint = json.loads(row["record_json"])["decision_checkpoint"]
                run_id = checkpoint.get("run_id") or "run_" + uuid4().hex
                active = ActiveRun(run_id, user, row["session_id"], source=row["source"])
                with self._active_runs_lock:
                    if run_id in self._active_runs:
                        continue
                    self._active_runs[run_id] = active
                request = {
                    **checkpoint, "user": user, "source": row["source"],
                    "session_id": row["session_id"], "run_id": run_id,
                    "_resume_decision_integration": row["integration_id"],
                    "_guidance_queue": active.guidance,
                    "_cancel_subagents_event": active.cancel_subagents_event,
                }

                def resume(active=active, request=request):
                    try:
                        for event in self.event_source(
                            request, root=self.root, cancel_event=active.cancel_event,
                        ):
                            if event.type == "error":
                                logging.getLogger(__name__).error(
                                    "决策等待恢复失败：%s", active.run_id,
                                )
                    except Exception:
                        logging.getLogger(__name__).exception("决策等待恢复失败")
                    finally:
                        with self._active_runs_lock:
                            self._active_runs.pop(active.run_id, None)

                try:
                    threading.Thread(target=resume, name="decision-resume", daemon=True).start()
                except BaseException:
                    with self._active_runs_lock:
                        self._active_runs.pop(active.run_id, None)
                    raise
