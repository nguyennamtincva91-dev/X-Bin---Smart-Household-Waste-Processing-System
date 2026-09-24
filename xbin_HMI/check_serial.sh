#!/usr/bin/env bash
# =====================================================================
#  X-BIN · Chẩn đoán serial Q509 <-> Arduino Mega 2560
#  Mục đích: tìm ĐÚNG lý do vì sao backend rớt xuống MOCK (máy không chạy).
#  Chạy:   bash check_serial.sh
# =====================================================================
set -u
echo "================ X-BIN SERIAL DOCTOR ================"
echo "Thời gian : $(date)"
echo "User      : $(whoami)"
echo "Nhóm      : $(id -nG)"
echo

# 1) pyserial -------------------------------------------------------
echo "----- [1] pyserial -----"
if python3 -c "import serial; print('OK pyserial', serial.VERSION)" 2>/dev/null; then
  :
else
  echo "  ✗ THIẾU pyserial  ->  pip install pyserial --break-system-packages"
fi
echo

# 2) Liệt kê cổng ---------------------------------------------------
echo "----- [2] Cổng serial đang có -----"
found=0
for p in /dev/ttyACM* /dev/ttyUSB* /dev/serial/by-id/* ; do
  [ -e "$p" ] || continue
  found=1
  ls -l "$p"
done
[ "$found" = 0 ] && echo "  ✗ KHÔNG thấy /dev/ttyACM* hay /dev/ttyUSB*  ->  Mega chưa cắm? Dây USB hỏng? Thử cắm lại."
echo

# 3) Quyền nhóm dialout --------------------------------------------
echo "----- [3] Quyền truy cập (dialout) -----"
if id -nG | grep -qw dialout; then
  echo "  ✓ User '$(whoami)' đã thuộc nhóm dialout."
else
  echo "  ✗ User '$(whoami)' CHƯA thuộc nhóm dialout (rất hay là nguyên nhân Permission denied)."
  echo "    Khắc phục:  sudo usermod -aG dialout $(whoami)   rồi ĐĂNG XUẤT/đăng nhập lại (hoặc reboot)."
fi
echo

# 4) Có tiến trình nào chiếm cổng không -----------------------------
echo "----- [4] Tiến trình đang giữ cổng -----"
for p in /dev/ttyACM* /dev/ttyUSB* ; do
  [ -e "$p" ] || continue
  who=""
  command -v fuser >/dev/null 2>&1 && who=$(fuser "$p" 2>/dev/null)
  if [ -z "$who" ] && command -v lsof >/dev/null 2>&1; then
    who=$(lsof "$p" 2>/dev/null | tail -n +2)
  fi
  if [ -n "$who" ]; then
    echo "  ⚠ $p đang bị giữ bởi: $who  (đóng Arduino Serial Monitor / app khác)"
  else
    echo "  ✓ $p không bị tiến trình nào giữ."
  fi
done
echo

# 5) Thử mở thật bằng pyserial -------------------------------------
echo "----- [5] Thử MỞ cổng @9600 (giống backend) -----"
python3 - <<'PY'
import os, glob
try:
    import serial
except Exception as e:
    print("  ✗ pyserial lỗi:", e); raise SystemExit
ports = []
env = os.environ.get("XBIN_SERIAL", "").strip()
if env: ports.append(env)
ports += [p for p in ["/dev/ttyACM0","/dev/ttyUSB0","/dev/serial0","/dev/ttyS0"] if p not in ports]
ports += [p for p in glob.glob("/dev/ttyACM*")+glob.glob("/dev/ttyUSB*") if p not in ports]
opened_any = False
for p in ports:
    if not os.path.exists(p):
        print(f"  - {p:16s}  (không tồn tại, bỏ qua)"); continue
    try:
        s = serial.Serial(p, 9600, timeout=0.2)
        s.close()
        print(f"  ✓ {p:16s}  MỞ ĐƯỢC  <-- dùng cổng này:  export XBIN_SERIAL={p}")
        opened_any = True
    except Exception as e:
        print(f"  ✗ {p:16s}  {e}")
print()
if opened_any:
    print("  KẾT LUẬN: Có cổng mở được -> backend SẼ chạy UART thật.")
    print("            Nếu vẫn MOCK: đảm bảo service chạy bằng đúng user (thuộc dialout)")
    print("            và set XBIN_SERIAL=<cổng ở trên> trước khi khởi động app.")
else:
    print("  KẾT LUẬN: KHÔNG cổng nào mở được -> backend buộc chạy MOCK (máy không chạy).")
    print("            Xem lý do từng dòng ✗ ở trên (quyền / không có cổng / bị chiếm).")
PY
echo
echo "================ HẾT ================"
