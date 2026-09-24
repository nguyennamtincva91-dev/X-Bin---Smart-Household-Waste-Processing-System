# X-BIN · HMI Touch Panel — Gói triển khai cho Q509 (Ubuntu 22.04)

Bộ deploy hoàn chỉnh cho thùng rác thông minh **X-BIN**, biến máy Q509 chạy Ubuntu 22.04 thành **màn hình HMI cảm ứng** điều khiển hệ thống.

```
xbin_deploy/
├── README.md              # File này
├── install.sh             # Cài đặt tự động (1 lệnh)
├── uninstall.sh           # Gỡ cài đặt
├── start_dev.sh           # Chạy backend ở chế độ dev (foreground)
├── start_kiosk.sh         # Chạy Chromium kiosk thủ công
├── requirements.txt       # Python deps
├── config.py              # Cấu hình (cổng UART, baudrate, web port,...)
├── hardware.py            # Lớp giao tiếp UART với MEGA (có mock fallback)
├── app.py                 # Backend Flask + SocketIO
├── static/
│   └── index.html         # Giao diện HMI 7-inch
└── systemd/
    ├── xbin-backend.service   # Autostart backend
    └── xbin-kiosk.service     # Autostart Chromium kiosk
```

---

## 1. Yêu cầu hệ thống

| Mục               | Yêu cầu                                              |
|-------------------|------------------------------------------------------|
| Phần cứng         | Q509 (hoặc tương đương) có màn hình cảm ứng          |
| OS                | Ubuntu 22.04 LTS (Desktop), kiến trúc x86_64 / arm64 |
| Mạng              | Kết nối Internet 1 lần để cài (`apt` + `pip`)        |
| Người dùng        | Có quyền `sudo`                                      |
| Phần cứng X-BIN   | (Tuỳ chọn) MEGA nối qua USB-UART; không có vẫn chạy được nhờ chế độ MOCK |

---

## 2. Cài đặt nhanh (1 lệnh)

Copy thư mục `xbin_HMI/` lên Q509 (qua USB, scp, git, ...), mở Terminal:

```bash
cd ~/xbin_HMI
sudo bash install.sh
```

Script sẽ tự động:

1. Cài `python3`, `python3-venv`, `chromium-browser`, `unclutter`, `xdotool`, `curl`
2. Tạo virtualenv tại `/opt/xbin/venv`, cài Flask + SocketIO + pyserial
3. Copy toàn bộ app vào `/opt/xbin/`
4. Tải `socket.io.min.js` vào `static/` (để chạy được khi offline)
5. Thêm user hiện tại vào group `dialout` (để truy cập `/dev/ttyUSB*`)
6. Cài systemd service **xbin-backend** (autostart on boot)
7. Đăng ký Chromium kiosk autostart khi user đăng nhập desktop
8. Tắt screen blanking + ẩn chuột (cảm giác HMI thật)
9. Khởi động backend ngay

Sau khi xong:

```bash
sudo reboot
```

→ Sau reboot, Q509 sẽ tự bật vào **fullscreen HMI** chạy ở `http://localhost:5000`.

---

## 3. Kiểm tra hoạt động

### 3.1 Kiểm tra backend
```bash
systemctl status xbin-backend       # Phải Active: active (running)
curl http://localhost:5000/health   # {"ok":true,"hw_mode":"mock"|"uart",...}
journalctl -u xbin-backend -f       # Log realtime
```

### 3.2 Kiểm tra kiosk thủ công (không reboot)
```bash
/opt/xbin/start_kiosk.sh            # Mở Chromium kiosk ngay
```

Thoát kiosk: **Alt+F4** hoặc **Ctrl+W**. Để mở Terminal khi đang kiosk: **Ctrl+Alt+T** hoặc **Ctrl+Alt+F2** chuyển TTY.

### 3.3 Kiểm tra từ thiết bị khác (debug)
Nếu Q509 đang chạy backend, máy khác trong cùng LAN có thể mở:
```
http://<IP_của_Q509>:5000
```
để xem cùng giao diện. Khoá lại trong `config.py` (đổi `HOST = "127.0.0.1"`) nếu chỉ muốn localhost.

---

## 4. Kết nối phần cứng Arduino

### 4.1 Cổng UART
Mặc định `hardware.py` thử mở theo thứ tự:
```
/dev/ttyUSB0    →  USB-to-UART (CH340, CP2102, FTDI)
/dev/ttyACM0    →  Arduino Uno/Nano (USB CDC)
/dev/serial0    →  GPIO UART (Pi-style header)
/dev/ttyS0      →  Cổng serial vật lý
```

Override bằng biến môi trường (sửa systemd unit):
```bash
sudo systemctl edit xbin-backend.service
```
Thêm:
```ini
[Service]
Environment=XBIN_SERIAL=/dev/ttyUSB1
```
Rồi:
```bash
sudo systemctl restart xbin-backend
```

### 4.2 Giao thức UART (JSON 1 dòng / lệnh)
**HMI → MEGA:**
```json
{"cmd":"start","bin":1}
{"cmd":"stop","bin":2}
{"cmd":"reset","bin":1}
{"cmd":"estop"}
{"cmd":"set","bin":1,"key":"press_threshold_kgf","value":15}
{"cmd":"set_mode","bin":2,"mode":"auto"}
```

**MEGA → HMI:**
```json
{"type":"state","bin":1,"state":"running"}            // ready|running|done|error
{"type":"step","bin":1,"step":"press","status":"active"} // active|done
{"type":"progress","bin":1,"pct":42.5}
{"type":"sensor","bin":1,"force":12.3,"pos":"extend","pump":"off"}
{"type":"sensor","bin":2,"rpm":1250,"sense":"object","bio":"off","door":"closed"}
{"type":"sysinfo","temp":28.4,"cpu":47.5}
{"type":"log","level":"info","msg":"...","source":"BIN-01"}
```

Mỗi message phải kết thúc bằng `\n`. Baudrate mặc định **115200 8N1**.
có thể chỉnh `SERIAL_BAUDRATE` trong `config.py`.

### 4.3 Khi chưa nối MEGA
Backend sẽ tự fallback **MOCK mode** — sinh dữ liệu giả lập đầy đủ để demo / test giao diện.
Bắt buộc dùng mock dù có cổng UART:
```bash
sudo systemctl edit xbin-backend.service
```
```ini
[Service]
Environment=XBIN_MOCK=1
```

---

## 5. Tùy chỉnh

### 5.1 Đổi cổng web / địa chỉ lắng nghe
Sửa `/opt/xbin/config.py`:
```python
HOST = "0.0.0.0"   # đổi "127.0.0.1" để chỉ localhost
PORT = 5000        # đổi sang 80 nếu cần (nhớ chạy với sudo hoặc setcap)
```
Rồi restart:
```bash
sudo systemctl restart xbin-backend
```

### 5.2 Đổi thông số xử lý mặc định
Cũng trong `config.py`, khối `DEFAULTS`. Ví dụ:
```python
"bin1": {"press_threshold_kgf": 15, "spray_seconds": 5, "timeout_seconds": 20}
```

### 5.3 Vô hiệu hoá kiosk autostart (giữ desktop)
```bash
rm ~/.config/autostart/xbin-kiosk.desktop
```
Vẫn có thể chạy thủ công bằng `/opt/xbin/start_kiosk.sh`.

### 5.4 Vô hiệu hoá backend autostart
```bash
sudo systemctl disable xbin-backend
sudo systemctl stop xbin-backend
```

---

## 6. Troubleshooting

| Triệu chứng                                  | Cách xử lý                                                                 |
|----------------------------------------------|----------------------------------------------------------------------------|
| Backend không lên                            | `journalctl -u xbin-backend -n 50 --no-pager` — đọc lỗi (thường là port 5000 đã dùng) |
| Kiosk hiện trang trắng                       | Backend chưa kịp boot — `start_kiosk.sh` đã chờ 30s; nếu vẫn lỗi, mở http://localhost:5000 trong Chromium bình thường để debug |
| Không thấy `/dev/ttyUSB0`                    | `dmesg | tail` để xem có nhận thiết bị USB-UART không, kiểm tra cáp + driver CH340 |
| `PermissionError: /dev/ttyUSB0`              | Logout/login lại sau install (group `dialout`) hoặc `sudo chmod 666 /dev/ttyUSB0` |
| HMI hiện "DEMO" thay vì "ONLINE"             | Backend không chạy, hoặc Chromium không vào được http://localhost:5000     |
| HMI hiện "ONLINE · MOCK"                     | Backend OK, nhưng không tìm thấy MEGA → dùng dữ liệu giả; nối MEGA + restart |
| Chromium hỏi "Restore pages?"                | Script đã xử lý, nhưng nếu vẫn hiện: xoá `~/.config/xbin-kiosk` rồi chạy lại |
| Touch screen không nhạy / sai vị trí         | Calibrate trong **Settings → Devices → Wacom Tablet** hoặc dùng `xinput-calibrator` |
| Màn tự tắt sau vài phút                      | Đảm bảo `xset s off; xset -dpms; xset s noblank` chạy — file `xbin-noblank.desktop` đã được tạo |

---

## 7. Gỡ cài đặt
```bash
sudo bash uninstall.sh
```
Sẽ gỡ services, autostart và `/opt/xbin`. Source code trong `~/xbin_deploy/` không bị xoá.

---

## 8. Kiến trúc hệ thống

```
   ┌─────────────────────────────────────────────────────────┐
   │  Q509 · Ubuntu 22.04 · Touchscreen                      │
   │                                                          │
   │   ┌──────────────────┐         ┌──────────────────┐     │
   │   │ Chromium Kiosk   │  HTTP   │ Flask + SocketIO │     │
   │   │ (HMI HTML)       │ ◄─────► │ (app.py)         │     │
   │   └──────────────────┘  WS     └────────┬─────────┘     │
   │                                          │ UART          │
   │                                          ▼               │
   │                            ┌─────────────────────────┐   │
   │                            │ hardware.py             │   │
   │                            │  - SerialHardware       │   │
   │                            │  - MockHardware (fb)    │   │
   │                            └────────────┬────────────┘   │
   └─────────────────────────────────────────┼────────────────┘
                                              │ /dev/ttyUSB0
                                              ▼
                                    ┌───────────────────┐
                                    │  MEGA 2560 R3     │
                                    │   - Loadcell      │
                                    │   - Motor ép      │
                                    │   - Cảm biến IR   │
                                    │   - Dao cắt       │
                                    │   - Bơm vi sinh   │
                                    │   - Cửa lật       │
                                    └───────────────────┘
```

---

## 9. Liên hệ

Đồ án tốt nghiệp X-BIN · HCMUTE 2026
Nhóm: Trần Quốc Thạnh · Trần Anh Trí · Nguyễn Nam Tín
GVHD: TS. Đặng Quang Khoa
