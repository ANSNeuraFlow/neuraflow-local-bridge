#!/usr/bin/env bash
# run.sh — aktywuje venv i uruchamia GUI
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo ".venv nie istnieje. Uruchom najpierw: ./setup.sh"
    exit 1
fi

source .venv/bin/activate
python bridge_gui.py "$@"
