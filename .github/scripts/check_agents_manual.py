"""Read-only, standard-library checks for the injected agents.md contract.

Never import runtime code, execute plugins, initialize users or edit documentation.
The contract describes selected critical claims, not a proof of every sentence.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ".github/agents-manual.contract.json"


def read_source(root: Path, relative: str) -> str:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"source escapes repository: {relative}")
    return path.read_text("utf-8-sig")


def headings(text: str) -> list[tuple[int, int, str]]:
    result = []
    fence = ""
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if match:
            token = match[1]
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not match[2].strip():
                fence = ""
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match and not fence:
            result.append((number, len(match[1]), match[2]))
    if fence:
        raise ValueError("unclosed Markdown fence")
    return result


def literal(node: ast.AST) -> Any:
    """Resolve literal numeric expressions without eval/importing the module."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)):
        left, right = literal(node.left), literal(node.right)
        if type(left) not in (int, float) or type(right) not in (int, float):
            raise ValueError("only numeric literal arithmetic is allowed")
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left // right
    return ast.literal_eval(node)


def python_node(text: str, symbol: str) -> ast.AST:
    tree = ast.parse(text)
    parts = symbol.split(".")
    body = tree.body
    for part in parts[:-1]:
        body = next(node.body for node in body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == part)
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == parts[-1]:
            return node
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == parts[-1] for target in targets):
            return node.value
    raise ValueError(f"declaration not found: {symbol}")


def source_fingerprint(text: str, symbol: str) -> str:
    """AST guard detects a behavior change and requires human re-verification."""
    tree = ast.dump(python_node(text, symbol), include_attributes=False)
    return hashlib.sha256(tree.encode()).hexdigest()


def fact_value(root: Path, source: dict[str, Any]) -> Any:
    text = read_source(root, source["path"])
    kind = source["kind"]
    if kind == "json":
        value = json.loads(text)
        for key in source.get("key", []):
            value = value[key]
    elif kind == "python":
        value = literal(python_node(text, source["symbol"]))
        for key in source.get("key", []):
            value = value[key]
    elif kind == "python_return":
        function = next(node for node in ast.walk(ast.parse(text)) if isinstance(node, ast.FunctionDef) and node.name == source["function"])
        returns = sorted((node for node in ast.walk(function) if isinstance(node, ast.Return)), key=lambda node: node.lineno)
        value = ast.literal_eval(returns[source["index"]].value)
    else:
        raise ValueError(f"unknown fact kind: {kind}")
    if "join" in source:
        value = source["join"].join(f"`{item}`" if source.get("backticks") else str(item) for item in value)
    if "divide" in source:
        value /= source["divide"]
    if isinstance(value, bool):
        return str(value).lower()
    return value


def render_claim(root: Path, rule: dict[str, Any]) -> str:
    facts = {name: fact_value(root, source) for name, source in rule.get("facts", {}).items()}
    return rule["claim"].format(**facts)


def plugin_name(root: Path, path: Path) -> str:
    text = path.read_text("utf-8-sig")
    marks = headings(text)
    starts = [(line, level) for line, level, title in marks if level == 2 and title.casefold() == "tool"]
    if len(starts) != 1:
        raise ValueError(f"one ## Tool required: {path.relative_to(root)}")
    start = starts[0][0]
    end = next((line for line, level, _ in marks if line > start and level <= 2), len(text.splitlines()) + 1)
    section = "\n".join(text.splitlines()[start:end - 1])
    blocks = re.findall(r"^\s*```json\s*\n(.*?)^\s*```\s*$", section, re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"one JSON tool block required: {path.relative_to(root)}")
    raw = json.loads(blocks[0])
    if not isinstance(raw, dict):
        raise ValueError(f"tool must be a JSON object: {path.relative_to(root)}")
    name = raw["name"]
    if not isinstance(name, str) or name != path.parent.name or not isinstance(raw.get("enabled"), bool):
        raise ValueError(f"invalid tool identity: {path.relative_to(root)}")
    for field in ("description", "version", "entrypoint"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise ValueError(f"tool {name}: missing {field}")
    if not isinstance(raw.get("input_schema"), dict) or raw["input_schema"].get("type") != "object":
        raise ValueError(f"tool {name}: object input_schema required")
    entry, separator, function = raw["entrypoint"].partition(":")
    target = (path.parent / entry).resolve()
    if not separator or not function or not target.is_relative_to(path.parent.resolve()) or not target.is_file():
        raise ValueError(f"tool {name}: invalid/missing entrypoint")
    return name


def check(root: Path, *, manual_text: str | None = None, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root.resolve()
    contract = contract if contract is not None else json.loads(read_source(root, CONTRACT))
    errors: list[str] = []
    if contract.get("schema_version") != 1:
        raise ValueError("unsupported manual contract schema")
    text = manual_text if manual_text is not None else read_source(root, contract["manual"])
    visible = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if len(text) > contract["max_characters"]:
        errors.append(f"budget: {len(text)} > {contract['max_characters']} characters")
    marks = headings(visible)
    actual_sections = [title for _, level, title in marks if level == 2]
    if actual_sections != contract["sections"]:
        errors.append("structure: h2 sections/order differ from contract")
    h3 = [title for _, level, title in marks if level == 3]
    if len(h3) != len(set(h3)):
        errors.append("structure: duplicate h3 titles")
    lines = visible.splitlines()
    for index, (line, level, title) in enumerate(marks):
        end = marks[index + 1][0] - 1 if index + 1 < len(marks) else len(lines)
        content = "\n".join(lines[line:end]).strip()
        next_level = marks[index + 1][1] if index + 1 < len(marks) else 0
        if not content and next_level <= level:
            errors.append(f"structure: empty section: {title}")
    for phrase in contract.get("required_phrases", []):
        if phrase not in visible:
            errors.append(f"required safety/discipline phrase missing: {phrase}")
    for phrase in contract.get("forbidden_phrases", []):
        if phrase in visible:
            errors.append(f"obsolete claim: {phrase}")
    blocks: dict[str, str] = {}
    for ident, body in re.findall(r"<!-- rule:([\w-]+) -->\s*(.*?)\s*<!-- /rule -->", text, re.S):
        if ident in blocks:
            errors.append(f"rule duplicated: {ident}")
        blocks[ident] = body
    rule_ids = [rule["id"] for rule in contract["rules"]]
    if len(rule_ids) != len(set(rule_ids)):
        errors.append("contract: duplicate rule ids")
    for ident in set(blocks) - set(rule_ids):
        errors.append(f"undeclared rule: {ident}")
    for rule in contract["rules"]:
        try:
            expected = render_claim(root, rule)
            if expected not in blocks.get(rule["id"], ""):
                errors.append(f"claim {rule['id']}: missing/stale; expected {expected}")
            for boundary in rule.get("boundaries", []):
                if boundary not in blocks.get(rule["id"], ""):
                    errors.append(f"claim {rule['id']}: boundary missing: {boundary}")
            for guard in rule.get("source_guards", []):
                actual = source_fingerprint(read_source(root, guard["path"]), guard["symbol"])
                if actual != guard["ast_sha256"]:
                    errors.append(f"claim {rule['id']}: source behavior changed; re-verify prose and tests: {guard['path']}::{guard['symbol']}")
        except (ValueError, KeyError, OSError, SyntaxError, StopIteration, TypeError, IndexError) as exc:
            errors.append(f"claim {rule['id']}: cannot resolve source: {exc}")
    inventory: dict[str, list[str]] = {}
    for group in contract["inventories"]:
        names = []
        for path in sorted(root.glob(group["glob"])):
            try:
                if group["kind"] == "plugin":
                    name = plugin_name(root, path)
                elif group["kind"] == "agent":
                    raw = json.loads(path.read_text("utf-8-sig"))
                    name = raw["name"]
                    if name != path.parent.name:
                        raise ValueError(f"agent name differs from directory: {path}")
                    for filename in ("AGENT.md", "agent-config.json", raw["trigger"]):
                        target = (path.parent / filename).resolve()
                        if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
                            raise ValueError(f"agent {name}: missing/invalid package entry: {filename}")
                elif group["kind"] == "system_job":
                    raw = json.loads(path.read_text("utf-8-sig"))
                    if raw.get("type") not in {"daily", "recurring", "weekly", "monthly", "once"}:
                        raise ValueError(f"invalid system job: {path}")
                    name = path.stem
                else:
                    raise ValueError(f"unknown inventory kind: {group['kind']}")
                names.append(name)
                if f"`{name}`" not in visible:
                    errors.append(f"coverage {group['kind']}: no explicit capability/entry for {name}")
            except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
                errors.append(f"inventory: {exc}")
        if not names:
            errors.append(f"inventory {group['kind']}: no definitions found")
        inventory[group["kind"]] = names
    runtime = {row["path"]: row for row in contract.get("runtime_paths", [])}
    external = {row["path"]: row for row in contract.get("external_paths", [])}
    for token in set(re.findall(r"`([^`\n]+)`", visible)):
        if not re.fullmatch(r"[.\w<>*-]+(?:/[.\w<>*()-]+)+/?", token) and not re.fullmatch(r"[\w.-]+\.(?:md|py|json|yml)", token):
            continue
        if token in external:
            if not external[token].get("owner"):
                errors.append(f"external path needs owner/repository classification: {token}")
        elif token.startswith(("users/", "tmp/", "runtime/")):
            row = runtime.get(token)
            if row is None:
                errors.append(f"runtime path not classified: {token}")
            elif row.get("proof_literal") and row["proof_literal"] not in read_source(root, row["source"]):
                errors.append(f"runtime path source no longer mentions directory: {token}")
        elif not (root / token).resolve().is_relative_to(root) or not (root / token).exists():
            errors.append(f"source path missing: {token}")
    scene_start = next((line for line, level, title in marks if level == 2 and title == contract["scene_section"]), None)
    if scene_start is not None:
        scene_end = next((line - 1 for line, level, _ in marks if line > scene_start and level == 2), len(lines))
        for line in lines[scene_start:scene_end]:
            if line.startswith("|") and len(line) > contract["max_scene_row_characters"]:
                errors.append(f"scene index: oversized mapping ({len(line)} chars)")
    return {"passed": not errors, "errors": errors, "characters": len(text),
            "max_characters": contract["max_characters"], "rules_checked": len(rule_ids),
            "inventory": inventory, "scope": "selected critical claims, structure, source paths and built-in reachability; not a semantic proof"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = check(args.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"passed": False, "errors": [str(exc)]}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
