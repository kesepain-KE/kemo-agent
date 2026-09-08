"""Shell-language detection without process or session side effects."""

from __future__ import annotations

import re

_WINDOWS_HEAD_PIPE_RE = re.compile(r"\|\s*head(?:\.exe)?(?:\s|$)", re.IGNORECASE)
_POWERSHELL_COMMAND_RE = re.compile(
    r"(?:(?:[A-Za-z_][A-Za-z0-9_.-]*)\\)?(?:Add|Clear|Compare|Connect|ConvertFrom|ConvertTo|Copy|Disable|Disconnect|"
    r"Enable|Enter|Exit|Export|Find|ForEach|Format|Get|Group|Import|Install|Invoke|"
    r"Join|Measure|Move|New|Out|Pop|Push|Read|Receive|Register|Remove|Rename|Resolve|"
    r"Restart|Resume|Save|Select|Send|Set|Show|Sort|Split|Start|Stop|Suspend|Test|"
    r"Trace|Unblock|Uninstall|Unregister|Update|Wait|Where|Write)-[A-Za-z][A-Za-z0-9-]*",
    re.IGNORECASE,
)
_POWERSHELL_ASSIGNMENT_RE = re.compile(
    r"^\s*\$(?:[A-Za-z_][A-Za-z0-9_]*:)?[A-Za-z_][A-Za-z0-9_]*\s*"
    r"(?:\+=|-=|\*=|/=|%=|=)",
    re.IGNORECASE,
)
_POWERSHELL_VALUE_RE = re.compile(
    r"(?:^|\s)\$(?:env:[A-Za-z_][A-Za-z0-9_]*|_)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_CMD_ENVIRONMENT_RE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%")
_SHELL_BOUNDARY_RE = re.compile(r"\|\||&&|[|;\r\n]")
_SHELL_MASK_CHAR = "\0"

def _mask_quoted_shell_content(command: str) -> tuple[str, bool]:
    """Mask quoted arguments without trying to parse either shell fully.

    The output remains the same length and preserves quote delimiters, so a
    quoted executable still occupies the first-command position.  Caret and
    backtick escapes are masked with their target.  Unclosed quotes simply mask
    the rest of the input; malformed input must never crash auto selection.
    """

    masked = list(command)
    quote = ""
    saw_cmd_escape = False
    index = 0
    while index < len(command):
        char = command[index]
        if not quote:
            if char in {'"', "'"}:
                quote = char
            elif char in {"^", "`"} and index + 1 < len(command):
                if char == "^":
                    saw_cmd_escape = True
                masked[index] = _SHELL_MASK_CHAR
                masked[index + 1] = _SHELL_MASK_CHAR
                index += 2
                continue
            elif char == "\\" and index + 1 < len(command) and command[index + 1] in {
                '"',
                "'",
            }:
                masked[index] = _SHELL_MASK_CHAR
                masked[index + 1] = _SHELL_MASK_CHAR
                index += 2
                continue
            index += 1
            continue

        if char in {"^", "`"} and index + 1 < len(command):
            masked[index] = _SHELL_MASK_CHAR
            masked[index + 1] = _SHELL_MASK_CHAR
            index += 2
            continue
        if char == "\\":
            run_end = index
            while run_end < len(command) and command[run_end] == "\\":
                masked[run_end] = _SHELL_MASK_CHAR
                run_end += 1
            if run_end < len(command) and command[run_end] == quote:
                if (run_end - index) % 2:
                    masked[run_end] = _SHELL_MASK_CHAR
                else:
                    quote = ""
                index = run_end + 1
                continue
            index = run_end
            continue
        if char == quote:
            quote = ""
        else:
            masked[index] = _SHELL_MASK_CHAR
        index += 1
    return "".join(masked), saw_cmd_escape


def _first_shell_word(segment: str) -> str:
    match = re.match(r"\s*(\S+)", segment)
    return match.group(1) if match else ""


def _looks_like_powershell(command: str) -> bool:
    """Recognize syntax that cmd.exe cannot safely interpret.

    Auto selection is deliberately conservative.  Generic executable commands
    stay on cmd.exe on Windows because quoted executable paths use different
    invocation syntax in PowerShell; commands with clear PowerShell syntax use
    the newest available PowerShell instead.
    """

    shell_text, saw_cmd_escape = _mask_quoted_shell_content(command)
    segments = _SHELL_BOUNDARY_RE.split(shell_text)
    first_words = [_first_shell_word(segment) for segment in segments]
    if saw_cmd_escape or _CMD_ENVIRONMENT_RE.search(shell_text):
        # These are unambiguous cmd.exe expansion/escaping signals.  Choosing
        # PowerShell could otherwise produce a successful but different command.
        return False
    if any(word.startswith(('"', "'")) for word in first_words):
        # PowerShell treats a bare quoted command-position token as a string.
        # Keep cmd compatibility unless the caller uses PowerShell's explicit
        # call operator (&), which appears as the first word instead.
        return False
    for segment, first_word in zip(segments, first_words):
        if not first_word:
            continue
        if first_word == "&" or re.match(r"^\.\s+\S", segment.lstrip()):
            return True
        if _POWERSHELL_COMMAND_RE.fullmatch(first_word):
            return True
        if first_word.casefold().endswith(".ps1"):
            return True
        if _POWERSHELL_ASSIGNMENT_RE.match(segment):
            return True
    return bool(
        _POWERSHELL_VALUE_RE.search(shell_text)
        or re.search(r"(?<!\S)@\s*[({]", shell_text)
    )
