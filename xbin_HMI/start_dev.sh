#!/usr/bin/env bash
# X-BIN · Chạy backend ở chế độ dev (foreground, debug log)
# Dùng để test khi chưa cài systemd.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Nếu đã cài /opt/xbin/venv thì dùng nó, ngược lại tạo venv cục bộ
if [[ -x /opt/xbin/venv/bin/python ]]; then
  PY=/opt/xbin/venv/bin/python
elif [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  echo "▸ Tạo venv cục bộ ./.venv ..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip wheel
  ./.venv/bin/pip install -r requirements.txt
  PY=./.venv/bin/python
fi

# Cho phép mock mode khi chưa nối STM32
export XBIN_MOCK="${XBIN_MOCK:-0}"
echo "▸ Backend khởi động http://localhost:5000  (mock=$XBIN_MOCK)"
exec "$PY" app.py
