"""Safe generated skill reuse and near-duplicate discovery."""
from __future__ import annotations

from difflib import SequenceMatcher
import re

from plugins._creator_security import is_link
from run.memory import digest


def _signature(body):
    # Headings/prompts are not useful semantic signals by themselves.
    return " ".join(line.strip() for line in body.casefold().splitlines()
                    if not line.lstrip().startswith("#"))


def resolve_generated_name(base, requested, body):
    """Reuse exact content; suffix collisions; never silently overwrite assets."""
    comparisons = []
    target = base / requested
    signature = _signature(body)
    for path in sorted(base.iterdir()) if base.is_dir() else []:
        file = path / "SKILL.md"
        if is_link(path) or is_link(file) or not path.is_dir() or not file.is_file():
            continue
        previous = file.read_text("utf-8-sig")
        previous_signature = _signature(previous)
        ratio = SequenceMatcher(None, signature[:20000], previous_signature[:20000]).ratio()
        # Token/bigram overlap also catches reordered near-identical procedures.
        tokens = set(re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]{2}", signature))
        old_tokens = set(re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]{2}", previous_signature))
        overlap = len(tokens & old_tokens) / max(1, len(tokens | old_tokens))
        if signature == previous_signature:
            return path.name, True, {"resolution": "reuse_identical", "matches": [path.name]}
        if ratio >= .78 or overlap >= .72:
            comparisons.append({"name": path.name, "similarity": round(max(ratio, overlap), 3)})
    if comparisons:
        # Semantic judgement belongs to the caller. Refuse silent near-duplicates
        # and return a structured review result without consuming a failure slot.
        return requested, False, {"resolution": "needs_similarity_review", "matches": comparisons}
    if target.exists() or is_link(target):
        suffix = "-" + digest(body)[:8]
        requested = requested[:64-len(suffix)] + suffix
        return requested, False, {"resolution": "suffix_collision", "matches": [target.name]}
    return requested, False, {"resolution": "new", "matches": []}
