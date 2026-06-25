#!/usr/bin/env bash
# One-command launcher. Creates (and repairs) a local virtualenv on first run, then
# starts the overlay. Safe to re-run: it heals a half-finished setup instead of crashing.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
PY="$VENV/bin/python"

# 1) Create the venv if it isn't there.
if [ ! -x "$PY" ]; then
    echo "Setting up virtualenv…"
    # Some distros ship python3 without ensurepip, which makes `python3 -m venv`
    # fail; fall back to a pip-less venv and bootstrap pip below.
    python3 -m venv "$VENV" 2>/dev/null || python3 -m venv --without-pip "$VENV"
fi

# 2) Make sure pip exists *inside* the venv. If the system Python lacks ensurepip
#    (you'll have seen "No module named ensurepip"), the venv comes up without pip;
#    bootstrap it so no apt/sudo is needed. Cleanest long-term fix on Debian/Ubuntu
#    is `sudo apt install python3-full`.
if ! "$PY" -m pip --version >/dev/null 2>&1; then
    echo "Bootstrapping pip into the venv…"
    if ! "$PY" -m ensurepip --upgrade >/dev/null 2>&1; then
        GETPIP="$(mktemp "${TMPDIR:-/tmp}/get-pip.XXXXXX.py")"
        "$PY" -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '$GETPIP')"
        "$PY" "$GETPIP"
        rm -f "$GETPIP"
    fi
fi

# 3) Make sure dependencies are present (also repairs a venv whose first install
#    never finished — the case where the old script silently skipped setup).
if ! "$PY" -c "import PySide6" >/dev/null 2>&1; then
    echo "Installing dependencies (PySide6)…"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r "$DIR/requirements.txt"
fi

exec "$PY" "$DIR/mtga_overlay.py" "$@"
