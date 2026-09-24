"""
X-BIN HMI · Cấu hình hệ thống
Chỉnh sửa các giá trị ở đây để khớp với phần cứng và môi trường triển khai.
"""
import os

# =========================================================
#  WEB SERVER
# =========================================================
HOST = "0.0.0.0"          # Lắng nghe trên mọi interface (LAN). Đổi thành "127.0.0.1" nếu chỉ muốn localhost.
PORT = 5000               # Cổng web. Kiosk Chromium sẽ mở http://localhost:5000
SECRET_KEY = "xbin-hcmute-2026-change-me"   # Nên đổi sang chuỗi ngẫu nhiên khi deploy thật
DEBUG = False             # True khi đang dev, False khi production

# =========================================================
#  UART · GIAO TIẾP VỚI ARDUINO MEGA 2560 R3
# =========================================================
# Mega cắm USB → trên Q509/Ubuntu thường lên /dev/ttyACM0 (USB-CDC).
# Nếu xài USB-UART rời (CH340, FTDI) thì là /dev/ttyUSB0.
SERIAL_PORTS = [
    "/dev/ttyACM0",   # Mega native USB — ưu tiên 1
    "/dev/ttyUSB0",   # USB-UART rời
    "/dev/serial0",
    "/dev/ttyS0",
]
# Override qua env:  export XBIN_SERIAL=/dev/ttyACM1
SERIAL_PORT_OVERRIDE = os.environ.get("XBIN_SERIAL", "").strip() or None

# Mega đang chạy Serial.begin(9600) — phải khớp
SERIAL_BAUDRATE = 9600
SERIAL_TIMEOUT  = 0.1     # giây · timeout đọc non-blocking
SERIAL_RECONNECT_S = 3.0  # giây · thử kết nối lại sau khi mất kết nối

# =========================================================
#  CHẾ ĐỘ MOCK (mô phỏng phần cứng khi chưa nối STM32)
# =========================================================
# True  → luôn dùng mock (bỏ qua UART) · thích hợp khi test trên máy không có hardware
# False → cố mở UART trước; nếu fail mới fallback sang mock
# Override:  export XBIN_MOCK=1
FORCE_MOCK = os.environ.get("XBIN_MOCK", "0") == "1"

# =========================================================
#  THÔNG SỐ XỬ LÝ MẶC ĐỊNH
# =========================================================
DEFAULTS = {
    "bin1": {
        "press_threshold_kgf": 12,   # ngưỡng lực ép (loadcell)
        "spray_seconds":       3,    # thời gian phun nước rửa
        "timeout_seconds":     15,   # timeout ép tối đa (an toàn)
    },
    "bin2": {
        "mode":          "manual",   # 'manual' hoặc 'auto'
        "cut_seconds":   8,          # thời gian cắt
        "bio_seconds":   2,          # thời gian phun vi sinh
        "door_seconds":  4,          # thời gian giữ cửa lật
        "auto_trigger_s": 5,         # auto-trigger khi cảm biến phát hiện rác liên tục N giây
    },
    "system": {
        "sound":   True,
        "lock":    True,
        "warn":    True,
        "demo":    False,
    },
}
