#!/usr/bin/env bash
# X-BIN · Gỡ cài đặt
set -e
[[ $EUID -ne 0 ]] && { echo "Chạy với: sudo bash uninstall.sh"; exit 1; }
APP_USER="${SUDO_USER:-$USER}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"

echo "▸ Dừng services..."
systemctl disable --now xbin-backend.service 2>/dev/null || true
systemctl disable --now xbin-kiosk.service 2>/dev/null || true
rm -f /etc/systemd/system/xbin-backend.service /etc/systemd/system/xbin-kiosk.service
systemctl daemon-reload

echo "▸ Gỡ autostart kiosk..."
rm -f "$APP_HOME/.config/autostart/xbin-kiosk.desktop"
rm -f "$APP_HOME/.config/autostart/xbin-noblank.desktop"

echo "▸ Xoá /opt/xbin..."
rm -rf /opt/xbin

echo "✓ Đã gỡ cài đặt X-BIN. (Group dialout vẫn giữ — gỡ thủ công nếu cần)"
