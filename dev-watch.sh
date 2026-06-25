#!/usr/bin/env bash
# Dev mode: auto-restart the overlay whenever mtga_overlay.py changes.
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"; PY="$VENV/bin/python"; APP="$DIR/mtga_overlay.py"
[ -x "$PY" ] || { echo "Run ./run.sh once first to create the venv."; exit 1; }
child=""; cleanup(){ [ -n "$child" ] && kill "$child" 2>/dev/null; exit 0; }
trap cleanup INT TERM EXIT
last=""
echo "[watch] watching $APP (Ctrl+C to stop)"
while true; do
    m=$(stat -c %Y "$APP" 2>/dev/null)
    if [ "$m" != "$last" ]; then
        last="$m"; [ -n "$child" ] && kill "$child" 2>/dev/null
        "$PY" "$APP" & child=$!; echo "[watch] (re)started (pid $child)"
    fi
    sleep 1
done
