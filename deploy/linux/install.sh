#!/usr/bin/env sh
set -eu
if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
# This file is the only shell-owned temporary file and is removed on exit.
BOOTSTRAP=$(mktemp "${TMPDIR:-/tmp}/kemo-bootstrap.XXXXXX")
trap 'rm -f -- "$BOOTSTRAP"' 0
trap 'exit 130' INT
trap 'exit 143' HUP TERM
"$PYTHON" -c 'import sys, urllib.request; urllib.request.urlretrieve("https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/bootstrap.py", sys.argv[1])' "$BOOTSTRAP"
# Piped one-liners have no interactive stdin; safe defaults are non-destructive.
"$PYTHON" "$BOOTSTRAP" --platform linux --yes "$@"
