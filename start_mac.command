#!/bin/zsh
# macOS: Finder からダブルクリック、または Terminal で実行
#   ./start_mac.command

set -euo pipefail
cd "$(dirname "$0")"

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
read -r "?Enter で閉じる "
exit 1
