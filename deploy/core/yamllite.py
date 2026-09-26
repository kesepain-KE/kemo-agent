"""Strict mapping/list YAML subset. No implicit dates, tags, anchors or objects.

JSON (a YAML subset) is used when writing merged configuration. Comments are
preserved when unchanged; a changed original is retained in the transaction.
"""
from __future__ import annotations

import json
import re
from .common import DeployError


def loads(text: str) -> dict:
    if text.lstrip().startswith('{'):
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise DeployError(f"Duplicate key: {key}")
                out[key] = value
            return out
        try:
            result = json.loads(text, object_pairs_hook=pairs)
        except ValueError as exc:
            raise DeployError(f"Invalid JSON/YAML: {exc}") from exc
        if not isinstance(result, dict):
            raise DeployError('Configuration must be a mapping')
        return result

    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if '\t' in raw:
            raise DeployError(f"YAML line {number}: tabs are not supported")
        quote = None
        escaped = False
        end = len(raw)
        for index, char in enumerate(raw):
            if quote:
                if char == quote and not escaped:
                    quote = None
                escaped = char == '\\' and not escaped and quote == '"'
            elif char in ('"', "'"):
                quote = char
            elif char == '#' and (index == 0 or raw[index - 1].isspace()):
                end = index
                break
        line = raw[:end].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent % 2:
            raise DeployError(f"YAML line {number}: indentation must use two spaces")
        lines.append((indent, line.strip(), number))

    def scalar(value, number):
        if value.startswith('"'):
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, str):
                    raise ValueError('expected string')
                return parsed
            except ValueError as exc:
                raise DeployError(f"YAML line {number}: invalid quoted string") from exc
        if value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise DeployError(f"YAML line {number}: unclosed string")
            return value[1:-1].replace("''", "'")
        if value in ('true', 'false', 'null'):
            return {'true': True, 'false': False, 'null': None}[value]
        if re.fullmatch(r'-?(0|[1-9]\d*)', value):
            return int(value)
        if not value or value[0] in '&*!|>{[' or ': ' in value:
            raise DeployError(f"YAML line {number}: unsupported scalar {value!r}")
        return value

    def block(index, indent):
        is_list = lines[index][1].startswith('- ')
        result = [] if is_list else {}
        while index < len(lines) and lines[index][0] >= indent:
            depth, value, number = lines[index]
            if depth != indent:
                raise DeployError(f"YAML line {number}: unexpected indentation")
            if is_list:
                if not value.startswith('- '):
                    raise DeployError(f"YAML line {number}: mixed mapping/list")
                result.append(scalar(value[2:].strip(), number))
                index += 1
                continue
            match = re.fullmatch(r'("(?:\\.|[^"\\])*"|\x27[^\x27]*\x27|[^:]+):(?:\s+(.*))?', value)
            if not match:
                raise DeployError(f"YAML line {number}: expected key: value")
            key = scalar(match[1].strip(), number)
            if not isinstance(key, str) or key in result:
                raise DeployError(f"YAML line {number}: invalid/duplicate key {key!r}")
            index += 1
            if match[2] is not None:
                result[key] = scalar(match[2], number)
            elif index < len(lines) and lines[index][0] == indent + 2:
                result[key], index = block(index, indent + 2)
            else:
                raise DeployError(f"YAML line {number}: missing nested value")
        return result, index

    if not lines or lines[0][0] != 0:
        raise DeployError('YAML line 1: expected root mapping')
    result, _ = block(0, 0)
    if not isinstance(result, dict):
        raise DeployError('Configuration must be a mapping')
    return result

