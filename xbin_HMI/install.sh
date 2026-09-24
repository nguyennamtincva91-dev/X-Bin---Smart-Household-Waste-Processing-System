#!/usr/bin/env bash
#
# X-BIN · Install script cho Q509 / Ubuntu 22.04
# ----------------------------------------------
# Chạy với quyền sudo:    sudo bash install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$USER}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
INSTALL_DIR="/opt/xbin"
VENV_DIR="$INSTALL_DIR/venv"

C_RESET="\033[0m"; C_GREEN="\033[1;32m"; C_YELLOW="\033[1;33m"; C_RED="\033[1;31m"; C_CYAN="\033[1;36m"
say()  { echo -e "${C_CYAN}▸${C_RESET} $*"; }
ok()   { echo -e "${C_GREEN}✓${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}⚠${C_RESET} $*"; }
fail() { echo -e "${C_RED}✗${C_RESET} $*"; exit 1; }

# 1) Phải chạy với sudo
if [[ $EUID -ne 0 ]]; then
  fail "Hãy chạy với sudo:   sudo bash install.sh"
fi

# 2) Cài system packages
say "Cập nhật apt và cài các gói hệ thống..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  python3 python3-venv python3-pip \
  chromium-browser unclutter xdotool \
  curl ca-certificates
ok "System packages OK"

# Một số bản Ubuntu dùng 'chromium' thay vì 'chromium-browser'
if ! command -v chromium-browser >/dev/null && command -v chromium >/dev/null; then
  ln -sf "$(command -v chromium)" /usr/local/bin/chromium-browser
fi

# 3) Copy app vào /opt/xbin
say "Cài app vào $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR"/{app.py,config.py,hardware.py,requirements.txt,static,start_kiosk.sh,start_dev.sh} "$INSTALL_DIR"/
chown -R "$APP_USER":"$APP_USER" "$INSTALL_DIR"
chmod +x "$INSTALL_DIR"/start_kiosk.sh "$INSTALL_DIR"/start_dev.sh
ok "Đã copy app"

# 4) Tạo venv + cài Python deps
say "Tạo virtualenv và cài Python packages..."
sudo -u "$APP_USER" python3 -m venv "$VENV_DIR"
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
ok "Python packages OK"

# 5) Download socket.io client vào static/ (để chạy offline được)
say "Tải socket.io client cho offline..."
SIO_URL="https://cdn.socket.io/4.7.5/socket.io.min.js"
if curl -fsSL --max-time 15 "$SIO_URL" -o "$INSTALL_DIR/static/socket.io.min.js"; then
  chown "$APP_USER":"$APP_USER" "$INSTALL_DIR/static/socket.io.min.js"
  ok "socket.io.min.js đã lưu cục bộ"
else
  warn "Không tải được socket.io (mạng?). Trình duyệt sẽ thử CDN khi mở."
fi

# 6) Cho user vào group dialout để truy cập /dev/ttyUSB*
say "Thêm $APP_USER vào group 'dialout' để truy cập UART..."
usermod -aG dialout "$APP_USER" || true
ok "$APP_USER ∈ dialout (logout/login để có hiệu lực)"

# 7) Cài systemd services
say "Cài đặt systemd services..."
TPL_BACKEND="$SCRIPT_DIR/systemd/xbin-backend.service"
TPL_KIOSK="$SCRIPT_DIR/systemd/xbin-kiosk.service"

# Backend service (chạy như user)
sed -e "s|@APP_USER@|$APP_USER|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    -e "s|@VENV_DIR@|$VENV_DIR|g" \
    "$TPL_BACKEND" > /etc/systemd/system/xbin-backend.service

# Kiosk service (chạy như user, cần X session)
sed -e "s|@APP_USER@|$APP_USER|g" \
    -e "s|@APP_HOME@|$APP_HOME|g" \
    -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
    "$TPL_KIOSK" > /etc/systemd/system/xbin-kiosk.service

systemctl daemon-reload
systemctl enable xbin-backend.service
ok "Backend service đã enable"

# 8) Kiosk autostart qua .desktop (cách an toàn cho X session GNOME/Xfce)
say "Đăng ký kiosk autostart cho user $APP_USER..."
AUTOSTART_DIR="$APP_HOME/.config/autostart"
sudo -u "$APP_USER" mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/xbin-kiosk.desktop" << EOF
[Desktop Entry]
Type=Application
Name=X-BIN Kiosk
Comment=X-BIN HMI Touch Panel
Exec=$INSTALL_DIR/start_kiosk.sh
X-GNOME-Autostart-enabled=true
NoDisplay=false
EOF
chown -R "$APP_USER":"$APP_USER" "$APP_HOME/.config/autostart"
ok "Kiosk sẽ autostart khi user đăng nhập desktop"

# 9) Tắt screen blanking (X11)
say "Tắt screen blanking & power save (sẽ áp dụng ở session sau)..."
sudo -u "$APP_USER" mkdir -p "$APP_HOME/.config/autostart"
cat > "$APP_HOME/.config/autostart/xbin-noblank.desktop" << EOF
[Desktop Entry]
Type=Application
Name=X-BIN · Disable Screen Blanking
Exec=sh -c 'xset s off; xset -dpms; xset s noblank'
X-GNOME-Autostart-enabled=true
EOF
chown "$APP_USER":"$APP_USER" "$APP_HOME/.config/autostart/xbin-noblank.desktop"
ok "Đã tắt screen blanking"

# 10) Khởi động backend ngay
say "Khởi động backend..."
systemctl restart xbin-backend.service
sleep 1.5
if systemctl is-active --quiet xbin-backend.service; then
  ok "Backend đang chạy"
else
  warn "Backend chưa chạy. Xem log:  journalctl -u xbin-backend -n 30 --no-pager"
fi

# Tóm tắt
echo
echo -e "${C_GREEN}═══════════════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_GREEN} X-BIN cài đặt HOÀN TẤT${C_RESET}"
echo -e "${C_GREEN}═══════════════════════════════════════════════════════════════${C_RESET}"
echo
echo "  Backend:   http://localhost:5000   (đã autostart on boot)"
echo "  Kiosk:     mở Chromium fullscreen khi $APP_USER đăng nhập desktop"
echo
echo "  Lệnh hữu ích:"
echo "    systemctl status xbin-backend       # xem trạng thái backend"
echo "    journalctl -u xbin-backend -f       # xem log realtime"
echo "    sudo systemctl restart xbin-backend # khởi động lại backend"
echo "    $INSTALL_DIR/start_kiosk.sh         # chạy kiosk thủ công"
echo "    $INSTALL_DIR/start_dev.sh           # chạy backend ở chế độ dev"
echo
echo "  Reboot để kiosk autostart:   sudo reboot"
echo
