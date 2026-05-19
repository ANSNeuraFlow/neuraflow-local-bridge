#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "NeuraflowLocalBridge - konfiguracja środowiska"
echo "========================================="

if ! command -v python3 &>/dev/null; then
    echo "Python3 nie znaleziony. Zainstaluj Python 3.9+ i spróbuj ponownie."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PYTHON_VERSION"

if [ ! -d ".venv" ]; then
    echo "→ Tworzę środowisko wirtualne (.venv)..."
    python3 -m venv .venv
    echo "✓ .venv gotowe"
else
    echo "✓ .venv już istnieje"
fi

source .venv/bin/activate

echo "→ Aktualizuję pip..."
pip install --upgrade pip --quiet

echo "→ Instaluję zależności..."
pip install -r requirements.txt

echo ""
echo "   Gotowe! Uruchom bridge:"
echo "   source .venv/bin/activate && python bridge_gui.py lub ./run.sh "

