#!/usr/bin/env bash
# One-command launcher. Creates a local virtualenv on first run, then starts the overlay.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
if [ ! -d "$VENV" ]; then
    echo "First run: setting up virtualenv + PySide6…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r "$DIR/requirements.txt"
fi
exec "$VENV/bin/python" "$DIR/mtga_overlay.py" "$@"
