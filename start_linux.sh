#!/usr/bin/env bash
# Linux: 実行権限を付けてダブルクリック、またはターミナルで
#   chmod +x start_linux.sh
#   ./start_linux.sh

set -euo pipefail
cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")"

if [[ -x ".venv/bin/python" ]]; then
  exec ".venv/bin/python" main.py
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 main.py
fi

echo "Python が見つかりません。"
echo "  python3 -m venv .venv"
echo "  source .venv/bin/activate"
echo "  python -m pip install -r requirements.txt"
read -r -p "Enter で閉じる "
exit 1
