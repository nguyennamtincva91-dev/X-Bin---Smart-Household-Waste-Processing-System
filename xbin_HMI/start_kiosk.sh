#!/usr/bin/env bash
# X-BIN · Khởi động Chromium ở chế độ Kiosk fullscreen
# Cần X11 session đang chạy (Ubuntu Desktop / Xfce / etc.)
set -e
URL="${XBIN_URL:-http://localhost:5000/?kiosk=1}"

# Đảm bảo có DISPLAY
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

# Tắt blanking + ẩn chuột (best-effort)
( xset s off; xset -dpms; xset s noblank ) >/dev/null 2>&1 || true
( unclutter -idle 0.3 -root & ) >/dev/null 2>&1 || true

# Đợi backend lên (tối đa 30s)
for i in $(seq 1 30); do
  if curl -fs "$URL" >/dev/null 2>&1 || curl -fs http://localhost:5000/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Dọn dấu hiệu crash để Chromium không hỏi "Restore pages?"
PROFILE="$HOME/.config/xbin-kiosk"
mkdir -p "$PROFILE"
PREFS_FILE="$PROFILE/Default/Preferences"
if [[ -f "$PREFS_FILE" ]]; then
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' "$PREFS_FILE" || true
  sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/'   "$PREFS_FILE" || true
fi

# Tìm Chromium
BROWSER=""
for c in chromium-browser chromium google-chrome chrome; do
  if command -v "$c" >/dev/null; then BROWSER="$c"; break; fi
done
[[ -z "$BROWSER" ]] && { echo "Không tìm thấy Chromium/Chrome"; exit 1; }

exec "$BROWSER" \
  --user-data-dir="$PROFILE" \
  --kiosk \
  --start-fullscreen \
  --no-first-run \
  --disable-translate \
  --disable-infobars \
  --disable-features=TranslateUI \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --noerrdialogs \
  --check-for-update-interval=31536000 \
  --password-store=basic \
  --autoplay-policy=no-user-gesture-required \
  "$URL"
