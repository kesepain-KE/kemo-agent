"""Generated-skill provenance and non-destructive lifecycle reconciliation.

References use stable SQLite ids; promotions transfer them across structural
merge/split operations. Ordinary edits/deletes intentionally do not transfer.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from run.memory.store import connection, database_path


def is_work_memory(content: str) -> bool:
    return bool(re.search(
        r"工具|命令|路径|配置|部署|架构|排查|修复|流程|脚本|协议|项目|工作流|技术偏好|"
        r"硬件|服务器|开发|代码|启动|用户技能|API|[A-Za-z]:\\|"
        r"\b(?:tool|command|config|deploy|debug|workflow|script|repair)\b", content, re.I))


def quality_errors(content: str) -> list[str]:
    """Require real procedural sections rather than accidental keyword hits."""
    from run.config import scan_markdown_structure
    structure = scan_markdown_structure(content)
    lines = content.splitlines()
    sections = {"when": [], "steps": [], "failure": []}
    for index, heading in enumerate(structure.headings):
        title = heading.text.casefold()
        end = structure.headings[index + 1].line if index + 1 < len(structure.headings) else len(lines)
        body = "\n".join(lines[heading.line + 1:end]).strip()
        if re.search(r"何时|适用|触发|when|usage|use case", title):
            sections["when"].append(body)
        if re.search(r"步骤|流程|操作|steps|procedure|workflow", title):
            sections["steps"].append(body)
        if re.search(r"失败|异常|故障|回滚|failure|troubleshoot|error|rollback", title):
            sections["failure"].append(body)
    errors = []
    if not any(len(body) >= 12 for body in sections["when"]):
        errors.append("生成技能必须有非空的何时使用/适用场景章节")
    def executable_steps(body):
        items = re.findall(r"(?m)^\s*(?:\d+[.)、]|[-*])\s*(.+)$", body)
        return sum(bool(re.search(
            r"读取|核对|检查|执行|运行|打开|验证|保存|比较|选择|停止|设置|安装|测试|确认|记录|"
            r"\b(?:read|check|run|execute|open|verify|save|compare|select|stop|set|install|test|record)\b",
            item, re.I)) for item in items)
    if not any(executable_steps(body) >= 2 for body in sections["steps"]):
        errors.append("生成技能必须有至少两个可执行步骤，不能只是事实陈述")
    if not any(len(body) >= 16 and re.search(
        r"如果|若|失败|异常|检查|停止|回滚|if|fail|check|stop|retry|rollback", body, re.I)
               for body in sections["failure"]):
        errors.append("生成技能必须说明失败时的检查、停止或恢复处理")
    return errors


def record_sources(root, user, skill_name: str, references: list[str], *, merge=False) -> dict:
    from run.memory import iso, utc_now
    if not isinstance(references, list) or not references or len(references) > 20:
        raise ValueError("自动生成技能必须绑定 1–20 条 source_memories")
    with connection(root, user, write=True) as db:
        rows = []
        if merge:
            rows.extend(db.execute(
                """SELECT f.* FROM memory_skill_sources s
                   JOIN memory_fragments f ON f.id=s.fragment_id AND f.content_hash=s.content_hash
                   WHERE s.skill_name=?""", (skill_name,)).fetchall())
        for ref in references:
            if not isinstance(ref, str):
                raise ValueError("source_memories 必须是记忆引用或文件名")
            filename = ref.split(":", 1)[-1]
            row = db.execute("SELECT * FROM memory_fragments WHERE filename_key=?",
                             (filename.casefold(),)).fetchone()
            if row is None:
                raise ValueError("来源记忆不存在，不能伪造技能溯源")
            if not any(r["id"] == row["id"] for r in rows):
                rows.append(row)
        db.execute("DELETE FROM memory_skill_sources WHERE skill_name=?", (skill_name,))
        for row in rows:
            db.execute("INSERT INTO memory_skill_sources VALUES(?,?,?,?)",
                       (skill_name, row["id"], row["filename"], row["content_hash"]))
        db.execute(
            """INSERT INTO memory_generated_skills VALUES(?,'active',NULL,?)
               ON CONFLICT(skill_name) DO UPDATE SET status='active', reason=NULL, updated_at=excluded.updated_at""",
            (skill_name, iso(utc_now())))
    return {"status": "active", "sources": [
        {"fragment_id": r["id"], "filename": r["filename"], "content_hash": r["content_hash"]}
        for r in rows]}


def transfer_sources(db, source_id: int, source_hash: str,
                     targets: list[tuple[int, str, str]]) -> None:
    # Structural promotion may carry a still-valid binding forward, but must
    # never revive a skill whose source was corrected before the promotion.
    skills = db.execute(
        "SELECT skill_name FROM memory_skill_sources WHERE fragment_id=? AND content_hash=?",
        (source_id, source_hash)).fetchall()
    for skill in skills:
        db.execute("DELETE FROM memory_skill_sources WHERE skill_name=? AND fragment_id=?",
                   (skill["skill_name"], source_id))
        for fragment_id, filename, content_hash in targets:
            # Preserve any already-stale binding to a merge destination.
            db.execute("INSERT OR IGNORE INTO memory_skill_sources VALUES(?,?,?,?)",
                       (skill["skill_name"], fragment_id, filename, content_hash))
    # Conversions not yet created must follow the exact same lineage as skills
    # that already exist. Otherwise splitting a promoted source loses retries.
    jobs = db.execute("SELECT work_key, payload_json FROM memory_work_items "
                      "WHERE work_type='skill' AND status!='completed'").fetchall()
    for job in jobs:
        state = json.loads(job["payload_json"])
        sources = state.get("current_sources", [])
        if not any(s["fragment_id"] == source_id and s["content_hash"] == source_hash
                   for s in sources):
            continue
        state["current_sources"] = [
            s for s in sources if s["fragment_id"] != source_id
        ] + [{"fragment_id": ident, "filename": name, "content_hash": fingerprint}
             for ident, name, fingerprint in targets]
        db.execute("UPDATE memory_work_items SET payload_json=? WHERE work_key=?",
                   (json.dumps(state, ensure_ascii=False), job["work_key"]))


def reconcile(root: Path, user: str) -> dict[str, dict]:
    """Called by cron, Prompt discovery and Web reads; never deletes a skill."""
    from run.memory import iso, utc_now
    if not (root / "users" / user).is_dir() or not database_path(root, user).is_file():
        return {}
    with connection(root, user, write=True) as db:
        rows = db.execute(
            """SELECT s.*, f.content_hash AS current_hash, f.expires_at, f.tier FROM memory_skill_sources s
               LEFT JOIN memory_fragments f ON f.id=s.fragment_id""").fetchall()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["skill_name"], []).append(row)
        result = {}
        for name, sources in grouped.items():
            invalid = [r for r in sources if r["current_hash"] != r["content_hash"]
                       or (r["tier"] != "permanent" and r["expires_at"]
                           and r["expires_at"] <= iso(utc_now()))]
            state = "stale" if invalid else "active"
            reason = "来源记忆已修正、到期或移出活跃记忆，请人工复核、更新或删除此技能" if invalid else None
            db.execute(
                """UPDATE memory_generated_skills SET status=?, reason=?, updated_at=?
                   WHERE skill_name=? AND (status!=? OR COALESCE(reason,'')!=COALESCE(?,''))""",
                (state, reason, iso(utc_now()), name, state, reason))
            result[name] = {"status": state, "reason": reason, "sources": [
                {"fragment_id": r["fragment_id"], "filename": r["filename"],
                 "content_hash": r["content_hash"]} for r in sources]}
        return result


def remove_skill_record(root, user, name):
    if not database_path(root, user).is_file():
        return
    with connection(root, user, write=True) as db:
        db.execute("DELETE FROM memory_skill_sources WHERE skill_name=?", (name,))
        db.execute("DELETE FROM memory_generated_skills WHERE skill_name=?", (name,))
