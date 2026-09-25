"""Interactive command dispatcher for the CLI facade."""
from __future__ import annotations
from typing import Any

def _interactive_command(
    prompt: str,
    *,
    root: Path,
    user: str,
    source: str,
    session_id: str,
    stdout: Any,
) -> tuple[bool, str]:
    from run.engine import compress_context, context_status
    from run.config import load_config
    from run.history import (
        clear_session, find_record, list_sessions, new_conversation_id,
        reserve_session, session_messages, set_active,
    )
    from run.memory import MemoryStore

    command, _, argument = prompt.partition(" ")
    command = command.lower()
    argument = argument.strip()
    if command == "/new":
        new_session = argument or new_conversation_id()
        if find_record(root, user, source, new_session) is not None:
            print(f"会话 ID 已存在，请换一个名称：{new_session}", file=stdout)
            return True, session_id
        _close_cli_session(root, user, source, session_id)
        reserve_session(
            root, user, source, new_session,
            active_key=f"cli:{user}" if source == "cli" else None,
        )
        print(f"已新建并切换会话：{new_session}", file=stdout)
        return True, new_session
    if command == "/use":
        if not argument:
            print("用法：/use <session>", file=stdout)
            return True, session_id
        target = find_record(root, user, source, argument)
        if target is None:
            print(f"会话不存在：{argument}", file=stdout)
            return True, session_id
        if str(target.get("lifecycle") or "") != "open":
            print(f"会话已经结束，只能查看历史，不能继续：{argument}", file=stdout)
            return True, session_id
        if argument != session_id:
            _close_cli_session(root, user, source, session_id)
            if source == "cli":
                set_active(root, user, f"cli:{user}", argument, source=source)
        print(f"已切换会话：{argument}", file=stdout)
        return True, argument
    if command == "/sessions":
        sessions = list_sessions(root, user, source)
        if not sessions:
            print("暂无已提交会话。", file=stdout)
        for item in sessions:
            marker = "*" if item["session_id"] == session_id else " "
            print(f"{marker} {item['session_id']} | rounds={item['rounds']} | {item['updated_at']}", file=stdout)
        return True, session_id
    if command == "/clear":
        clear_session(root, user, source, session_id)
        print(f"已清空会话：{session_id}", file=stdout)
        return True, session_id
    if command == "/history":
        messages = session_messages(root, user, source, session_id)
        if not messages:
            print("当前会话暂无历史。", file=stdout)
        for message in messages:
            print(f"{message.get('role', '?')}: {message.get('content', '')}", file=stdout)
        return True, session_id
    if command == "/status":
        try:
            status = context_status(
                {"user": user, "source": source, "session_id": session_id}, root=root
            )
        except Exception:
                        # 保持传输级别状态在最小/测试工作空间中有用
                        # 尚未包含全局配置。
            sessions = {
                item["session_id"]: item for item in list_sessions(root, user, source)
            }
            rounds = sessions.get(session_id, {}).get("rounds", 0)
            print(
                f"user={user} | source={source} | session={session_id} | rounds={rounds}",
                file=stdout,
            )
            return True, session_id
        context = status["context"]
        print(
            f"user={user} | source={source} | session={session_id} | "
            f"rounds={status['rounds']} | context≈{context['estimated_tokens_before']}/"
            f"{context['input_budget']} | kept={context['rounds_kept']} | "
            f"removed={context['rounds_removed']} | summary={status['summary_cache_exists']}",
            file=stdout,
        )
        return True, session_id
    if command == "/compress":
        result = compress_context(
            {"user": user, "source": source, "session_id": session_id}, root=root
        )
        context = result["context"]
        summary = context["summary"]
        print(
            f"上下文整理完成：removed={context['rounds_removed']} | "
            f"kept={context['rounds_kept']} | cache_hit={summary['cache_hit']} | "
            f"generated={summary['generated']} | failed={summary['failed']}",
            file=stdout,
        )
        return True, session_id
    if command == "/memory":
        store = MemoryStore(root, user, load_config(user, root))
        items = store.list_items()
        if not items:
            print("暂无记忆。", file=stdout)
        for item in items:
            print(
                f"{item['filename']} | {item['tier']} | weight={item['weight']} | "
                f"{item['content']}",
                file=stdout,
            )
        return True, session_id
    if command == "/remember":
        if not argument:
            print("用法：/remember <内容>", file=stdout)
            return True, session_id
        store = MemoryStore(root, user, load_config(user, root))
        result = store.upsert_candidates(
            [{
                "content": argument,
                "explicit": True,
                "action": "upsert",
            }],
            source={"source": source, "session_id": session_id, "explicit": True},
        )
        if result["rejected"]:
            print("记忆内容为空或包含敏感凭据，未保存。", file=stdout)
        else:
            filename = (result["created"] or result["updated"])[0]
            print(f"已保存永久记忆：{filename}", file=stdout)
        return True, session_id
    if command == "/forget":
        if not argument:
            print("用法：/forget <记忆ID或关键词>", file=stdout)
            return True, session_id
        store = MemoryStore(root, user, load_config(user, root))
        removed = store.forget(argument)
        print(f"已删除 {len(removed)} 条记忆。", file=stdout)
        return True, session_id

        # --- 任务计划命令 ---
    if command == "/plans":
        from run.tasks import list_plans
        plans = list_plans(root, user)
        if not plans:
            print("暂无任务计划。", file=stdout)
        for item in plans:
            done = sum(1 for s in item.get("steps", []) if s.get("status") == "completed")
            total = len(item.get("steps", []))
            print(
                f"{item['plan_id']} | {item.get('status', '?')} | "
                f"{done}/{total} | {item.get('title', '')}",
                file=stdout,
            )
        return True, session_id
    if command == "/plan":
        if not argument:
            print("用法：/plan <目标描述>", file=stdout)
            return True, session_id
        from run.tasks import generate_plan, PlanGenerationError, PlanSkipped
        from run.tasks import PlanStore
        try:
            plan = generate_plan(
                root=root,
                user=user,
                goal=argument,
                source=source,
                session_id=session_id,
            )
        except PlanSkipped as exc:
            print(f"不需要创建计划：{exc}", file=stdout)
            return True, session_id
        except PlanGenerationError as exc:
            print(f"计划生成失败：{exc}", file=stdout)
            return True, session_id
        store = PlanStore(root, user)
        created = store.create(plan)
        print(
            f"已创建计划：{created['plan_id']} | {created['title']} | "
            f"{len(created['steps'])} 步 | 状态：{created['status']}",
            file=stdout,
        )
        for step in created["steps"]:
            deps = ", ".join(step.get("depends_on") or []) or "无"
            print(
                f"  {step['step_id']} | {step.get('tool_name', '无工具')} | "
                f"deps={deps} | {step['title']}",
                file=stdout,
        )
        if created.get("auto_accept"):
            print(
                "auto_accept 已开启，计划已批准；使用 /plan-approve 进入正式计划执行器。",
                file=stdout,
            )
        else:
            if created.get("reminder"):
                print(created["reminder"], file=stdout)
            print("使用 /plan-approve 批准执行。", file=stdout)
        return True, session_id
    if command == "/plan-show":
        if not argument:
            print("用法：/plan-show <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import get_plan
        try:
            plan = get_plan(root, user, argument)
        except Exception as exc:
            print(f"读取计划失败：{exc}", file=stdout)
            return True, session_id
        print(
            f"{plan['plan_id']} | {plan['status']} | {plan['title']}",
            file=stdout,
        )
        print(f"描述：{plan.get('description', '')}", file=stdout)
        for step in plan["steps"]:
            deps = ", ".join(step.get("depends_on") or []) or "无"
            print(
                f"  {step['step_id']} | {step['status']} | "
                f"{step.get('tool_name', '无工具')} | deps={deps} | "
                f"{'关键' if step.get('critical') else '非关键'} | {step['title']}",
                file=stdout,
            )
            if step.get("error"):
                print(f"    错误：{step['error'].get('message', '')}", file=stdout)
        return True, session_id
    if command == "/plan-approve":
        if not argument:
            print("用法：/plan-approve <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import approve_plan, execute_plan, get_plan
        from run.config import load_config
        try:
            current = get_plan(root, user, argument)
            plan = (
                current
                if current.get("status") == "approved"
                else approve_plan(root, user, argument)
            )
        except Exception as exc:
            print(f"批准失败：{exc}", file=stdout)
            return True, session_id
        print(
            f"计划 {argument} 已批准，进入正式计划执行器...",
            file=stdout,
        )
        config = load_config(user, root)
        for event in execute_plan(
            root=root, user=user, plan_id=argument, config=config,
        ):
            if event.type == "tool_call_start":
                print(f"  [{event.metadata.get('step_id', '')}] 开始：{event.tool_name}", file=stdout)
            elif event.type == "tool_call_result":
                status = event.metadata.get("status", "?")
                print(f"  [{event.metadata.get('step_id', '')}] {status}", file=stdout)
            elif event.type == "done":
                status = event.metadata.get("status", "")
                if status:
                    print(f"计划 {argument} → {status}", file=stdout)
            elif event.type == "error":
                detail = event.error or {}
                print(f"  错误：{detail.get('message', '')}", file=stdout)
        return True, session_id
    if command == "/plan-pause":
        if not argument:
            print("用法：/plan-pause <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import pause_plan
        try:
            pause_plan(root, user, argument)
            print(f"已暂停计划 {argument}。", file=stdout)
        except Exception as exc:
            print(f"暂停失败：{exc}", file=stdout)
        return True, session_id
    if command == "/plan-resume":
        if not argument:
            print("用法：/plan-resume <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import resume_plan, execute_plan
        from run.config import load_config
        try:
            resume_plan(root, user, argument)
        except Exception as exc:
            print(f"恢复失败：{exc}", file=stdout)
            return True, session_id
        print(f"已恢复计划 {argument}，继续执行...", file=stdout)
        config = load_config(user, root)
        for event in execute_plan(
            root=root, user=user, plan_id=argument, config=config,
        ):
            if event.type == "tool_call_start":
                print(f"  [{event.metadata.get('step_id', '')}] 开始：{event.tool_name}", file=stdout)
            elif event.type == "tool_call_result":
                status = event.metadata.get("status", "?")
                print(f"  [{event.metadata.get('step_id', '')}] {status}", file=stdout)
            elif event.type == "done":
                status = event.metadata.get("status", "")
                if status:
                    print(f"计划 {argument} → {status}", file=stdout)
            elif event.type == "error":
                detail = event.error or {}
                print(f"  错误：{detail.get('message', '')}", file=stdout)
        return True, session_id
    if command == "/plan-cancel":
        if not argument:
            print("用法：/plan-cancel <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import cancel_plan
        try:
            cancel_plan(root, user, argument)
            print(f"已取消计划 {argument}。", file=stdout)
        except Exception as exc:
            print(f"取消失败：{exc}", file=stdout)
        return True, session_id

        # --- cron 命令 ---
    if command == "/crons":
        from run.scheduler import CronStore
        store = CronStore(root, user)
        tasks = store.list_tasks()
        if not tasks:
            print("暂无定时任务。", file=stdout)
        for item in tasks:
            print(
                f"{item['task_id']} | {item.get('status', '?')} | "
                f"{item.get('type', '?')} | "
                f"next={item.get('next_run_at', '')} | {item.get('title', '')}",
                file=stdout,
            )
        return True, session_id
    if command == "/cron":
        if not argument:
            print("用法：/cron <自然语言定时要求>", file=stdout)
            return True, session_id
        from cron.service import generate_cron_task, CronGenerationError, CronSkipped
        from run.scheduler import CronStore
        try:
            task = generate_cron_task(
                root=root, user=user, user_request=argument,
                source=source, session_id=session_id,
            )
        except CronSkipped as exc:
            print(f"不需要创建定时任务：{exc}", file=stdout)
            return True, session_id
        except CronGenerationError as exc:
            print(f"定时任务生成失败：{exc}", file=stdout)
            return True, session_id
        store = CronStore(root, user)
        created = store.create(task)
        print(
            f"已创建定时任务：{created['task_id']} | {created['title']} | "
            f"{created['type']} | next={created['next_run_at']}",
            file=stdout,
        )
        return True, session_id
    if command == "/cron-show":
        if not argument:
            print("用法：/cron-show <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        store = CronStore(root, user)
        try:
            task = store.read(argument)
        except Exception as exc:
            print(f"读取任务失败：{exc}", file=stdout)
            return True, session_id
        print(
            f"{task['task_id']} | {task['status']} | {task['title']}",
            file=stdout,
        )
        detail = task.get("time") if task.get("type") == "daily" else task.get("interval_seconds", "")
        print(f"调度：{task.get('type', '')} {detail}", file=stdout)
        print(f"下一次执行：{task.get('next_run_at', '')}", file=stdout)
        print(f"最近执行：{task.get('latest_run_at', '')}", file=stdout)
        return True, session_id
    if command == "/cron-pause":
        if not argument:
            print("用法：/cron-pause <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        store = CronStore(root, user)
        try:
            store.update(argument, lambda t: {**t, "status": "paused"})
            print(f"已暂停定时任务 {argument}。", file=stdout)
        except Exception as exc:
            print(f"暂停失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-resume":
        if not argument:
            print("用法：/cron-resume <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        from cron.schedule import compute_next_run
        store = CronStore(root, user)
        def _resume(t):
            t["status"] = "enabled"
            t["next_run_at"] = compute_next_run(t)
            return t
        try:
            store.update(argument, _resume)
            print(f"已恢复定时任务 {argument}。", file=stdout)
        except Exception as exc:
            print(f"恢复失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-cancel":
        if not argument:
            print("用法：/cron-cancel <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        store = CronStore(root, user)
        try:
            store.update(argument, lambda t: {**t, "status": "cancelled"})
            print(f"已取消定时任务 {argument}。", file=stdout)
        except Exception as exc:
            print(f"取消失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-run":
        if not argument:
            print("用法：/cron-run <任务ID>", file=stdout)
            return True, session_id
        from cron.executor import execute_cron_task
        from run.config import load_config
        config = load_config(user, root)
        print(f"立即执行定时任务 {argument}...", file=stdout)
        try:
            result = execute_cron_task(
                root=root, user=user, task_id=argument, config=config,
            )
            print(f"完成：{result.get('status', '?')}", file=stdout)
        except Exception as exc:
            print(f"执行失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-start":
        from cron.scheduler import CronScheduler
                # 使用模块级单例
        if not hasattr(run_interactive, '_cron_scheduler'):
            run_interactive._cron_scheduler = CronScheduler(root)
        sched = run_interactive._cron_scheduler
        if sched.running:
            print("调度器已在运行。", file=stdout)
        else:
            sched.start()
            print("调度器已启动。", file=stdout)
        return True, session_id
    if command == "/cron-stop":
        if hasattr(run_interactive, '_cron_scheduler'):
            sched = run_interactive._cron_scheduler
            sched.stop()
            print("调度器已停止。", file=stdout)
        else:
            print("调度器未运行。", file=stdout)
        return True, session_id

    return False, session_id

_IMPLEMENTATION = _interactive_command

def configure(context: dict[str, Any]) -> None:
    for name, value in context.items():
        if name.startswith("__") or name == "_interactive_command":
            continue
        globals()[name] = value

def invoke(*args: Any, **kwargs: Any) -> Any:
    return _IMPLEMENTATION(*args, **kwargs)
