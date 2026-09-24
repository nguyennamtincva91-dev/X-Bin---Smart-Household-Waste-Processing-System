"""
X-BIN · Lớp giao tiếp phần cứng — Arduino Mega 2560 R3

Protocol thực tế (theo code Mega):
  - Serial 9600 baud, lệnh 1 ký tự ASCII
  - Mega trả về text Việt thường (không phải JSON), backend parse text

Lệnh HMI gửi xuống Mega:
  s : bắt đầu chu kỳ ép (ngăn vô cơ)
  r : reset chu kỳ ép
  1 / 3 / 5 : chọn thời gian cắt (phút)
  y : bắt đầu motor cắt
  z : dừng motor cắt
  x : mở cửa lật 90°
  g : đóng cửa lật
  l : in trạng thái mức dung dịch

Bin 2 (ngăn hữu cơ) — chu kỳ host orchestrate vì Mega chỉ có lệnh con:
  digit → y → (Mega cắt xong tự dừng) → bio (fake delay vì chưa có bơm) → x → wait → g

Nếu chưa nối Mega thì rơi xuống MockHardware để UI vẫn chạy demo.
"""

import json
import logging
import os
import re
import threading
import time
import random
from typing import Callable, Optional

try:
    import serial            # pyserial
    HAS_SERIAL = True
except Exception:
    HAS_SERIAL = False

import config

log = logging.getLogger("xbin.hw")

# (X-BIN) chẩn đoán lý do mở serial thất bại
def _serial_fail_reason(port: str, e: Exception) -> str:
    """Dịch lỗi mở serial sang nguyên nhân dễ hiểu để biết vì sao rớt MOCK."""
    import errno as _errno
    msg = str(e)
    eno = getattr(e, "errno", None)
    if eno == _errno.EACCES or "Permission denied" in msg:
        return (f"Bị từ chối quyền (Permission denied). "
                f"Thêm user vào nhóm dialout:  sudo usermod -aG dialout $USER  rồi đăng nhập lại.")
    if eno == _errno.ENOENT or "No such file" in msg or "could not open port" in msg.lower():
        return (f"Không thấy cổng {port}. Cắm Mega chưa? Kiểm tra:  ls /dev/ttyACM* /dev/ttyUSB*  "
                f"và set đúng cổng:  export XBIN_SERIAL=/dev/ttyXXX")
    if eno == _errno.EBUSY or "Device or resource busy" in msg or "Access is denied" in msg:
        return ("Cổng đang bị chiếm (Serial Monitor của Arduino IDE còn mở?). Đóng app khác đang giữ cổng.")
    return msg


# =============================================================
#  BASE — interface chung cho UART thật và Mock
# =============================================================
class _Base:
    mode = "?"

    def __init__(self, on_event: Callable[[dict], None]):
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.connected = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"hw-{self.mode}")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def send(self, cmd: dict):
        raise NotImplementedError

    def _loop(self):
        raise NotImplementedError

    def _emit(self, event: dict):
        try:
            self.on_event(event)
        except Exception as e:
            log.exception("on_event callback failed: %s", e)


# =============================================================
#  MEGA HARDWARE — giao tiếp serial với Arduino Mega 2560
# =============================================================
class MegaHardware(_Base):
    """
    Map giữa HMI command (JSON-like dict) ↔ Mega ASCII command.
    Có 1 background thread đọc serial liên tục parse text Mega đẩy lên,
    1 thread mỗi bin để orchestrate chu kỳ (bin 2 cần xếp nhiều bước).
    """
    mode = "uart"

    # Regex để bắt thông tin từ text Mega trả về.
    # Mỗi pattern em test với output cụ thể trong code Mega.
    RE_FORCE        = re.compile(r"Luc nen:\s*([\d.]+)\s*g")
    RE_LEVEL_DET    = re.compile(r"LEVEL_DETERGENT_(OK|LOW|CHECKING)")
    RE_LEVEL_MIC    = re.compile(r"LEVEL_MICROBE_(OK|LOW|CHECKING)")
    # Cam bien sieu am JSN-SR04T bao day thung rac (Mega code moi).
    # Mega gui:
    #   BIN_INORGANIC_BIN_FULL / _OK / _CHECKING   -> ngan vo co
    #   BIN_ORGANIC_BIN_FULL   / _OK / _CHECKING   -> ngan huu co
    RE_BIN_FILL     = re.compile(r"BIN_(INORGANIC|ORGANIC)_BIN_(FULL|OK|CHECKING)")

    def __init__(self, on_event, port: str, baudrate: int = 9600):
        super().__init__(on_event)
        self.port = port
        self.baudrate = baudrate
        self._ser: Optional["serial.Serial"] = None
        self._tx_lock = threading.Lock()

        # State cho orchestrator
        self._cycle_threads = {1: None, 2: None}
        self._stop_flags = {1: threading.Event(), 2: threading.Event()}

        # Threading.Event mà parser bật khi gặp mốc quan trọng
        # → orchestrator thread chờ event này thay vì poll
        self._evt_press_threshold = threading.Event()   # "DAT NGUONG -> DUNG EP"
        self._evt_press_limit     = threading.Event()   # "DA CHAM CONG TAC HANH TRINH"
        self._evt_cut_done        = threading.Event()   # "DUNG MOTOR CAT"
        self._evt_door_open       = threading.Event()   # "Da mo cua 90 do"
        self._evt_door_close      = threading.Event()   # "Da dong cua"
        self._evt_auto_trigger    = threading.Event()   # "AUTO_TRIGGER" (Mega gửi khi sensor 5s ở auto)
        self._evt_pump_done       = threading.Event()   # "DUNG BOM" (Mega gửi khi bơm hết thời gian)
        # Cờ riêng để abort sub-cycle hiện tại trong auto mode mà KHÔNG tắt auto.
        # User bấm STOP trong auto → set cờ này → sub-cycle thoát, loop tiếp tục đợi trigger.
        self._evt_subcycle_abort  = threading.Event()

        # Auto mode: chỉ bin 2 có. True = đang "armed" chờ sensor 5s
        self._auto_armed_bin2 = False

        # Thread poll mức đầy thùng rác (JSN-SR04T) — gửi 'u' cho Mega mỗi 3s.
        # Mega đáp ngay bằng BIN_*_FULL/OK/CHECKING -> parser bắt -> event "fill" cho UI.
        # User yêu cầu: UI auto-update mỗi 3s không cần thao tác.
        self._fill_poll_thread: Optional[threading.Thread] = None
        self.FILL_POLL_INTERVAL_S = 3.0

        self._params = {
            1: {"spray_seconds": 3},
            2: {"cut_minutes": 1, "bio_seconds": 2, "door_seconds": 4, "mode": "manual"},
        }

    # ---------- serial mở/đóng ----------
    def _open(self) -> bool:
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            # Sau khi mở USB-CDC, Mega thường reset (DTR). Đợi 2s cho Mega boot xong.
            time.sleep(2.0)
            self._ser.reset_input_buffer()
            self.connected = True
            log.info("Mega UART mở OK: %s @ %d", self.port, self.baudrate)
            self._emit({"type": "log", "level": "success",
                        "msg": f"Kết nối Mega trên {self.port} @ {self.baudrate}"})
            # Start poll mức đầy ngay khi UART vừa mở.
            # Thread tự thoát khi self._stop set hoặc khi connected=False.
            self._start_fill_poll()
            return True
        except Exception as e:
            self.connected = False
            reason = _serial_fail_reason(self.port, e)
            log.warning("Mega UART mở fail (%s): %s", self.port, reason)
            self._emit({"type": "log", "level": "warn",
                        "msg": f"Không mở được {self.port}: {reason}"})
            return False

    def _start_fill_poll(self):
        """Spawn (idempotent) thread gửi 'u' mỗi 3s -> Mega in BIN_*_FULL/OK."""
        if self._fill_poll_thread and self._fill_poll_thread.is_alive():
            return  # đã chạy
        t = threading.Thread(target=self._fill_poll_loop, daemon=True,
                             name="fill-poll")
        self._fill_poll_thread = t
        t.start()

    def _fill_poll_loop(self):
        """
        Gửi 'u' cho Mega mỗi FILL_POLL_INTERVAL_S giây để hỏi trạng thái 2 thùng rác.
        Mega đáp ngay bằng 4 dòng (BIN_*_FULL/OK + text) → parser bắt → UI update.
        Tự thoát khi mất kết nối hoặc service tắt; sẽ được start lại khi _open() chạy lại.
        """
        log.info("Bắt đầu poll mức đầy thùng rác (chu kỳ %.1fs)", self.FILL_POLL_INTERVAL_S)
        while not self._stop.is_set():
            if self.connected:
                try:
                    self._send_char('u')
                except Exception as e:
                    log.debug("Fill poll send fail: %s", e)
            # Sleep dạng interruptible — service tắt sẽ break ngay
            if self._stop.wait(timeout=self.FILL_POLL_INTERVAL_S):
                break
        log.info("Dừng poll mức đầy thùng rác")

    def _close(self):
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._ser = None
        self.connected = False

    def _send_char(self, ch: str):
        """Gửi 1 ký tự (kèm \\n cho Serial Monitor truyền thống nhưng Mega thực ra không cần)."""
        if not self.connected or not self._ser:
            log.warning("UART chưa nối, drop char: %r", ch)
            return
        with self._tx_lock:
            try:
                self._ser.write(ch.encode("ascii"))
                self._ser.flush()
                log.debug("→ Mega: %r", ch)
            except Exception as e:
                log.error("UART write fail: %s", e)
                self._close()

    # ---------- API public ----------
    def send(self, cmd: dict):
        c = cmd.get("cmd")
        bin_id = cmd.get("bin")

        if c == "start":
            if bin_id == 1: self._start_bin1()
            elif bin_id == 2: self._start_bin2()

        elif c == "stop":
            if bin_id == 1: self._stop_bin1()
            elif bin_id == 2: self._stop_bin2()

        elif c == "reset":
            # Mega TuDong không có RESET state riêng — chỉ stop + đưa state về ready.
            if bin_id == 1: self._stop_bin1()
            elif bin_id == 2: self._stop_bin2()
            self._emit({"type": "state", "bin": bin_id, "state": "ready"})

        elif c == "estop":
            # tắt hết các cơ cấu
            self._send_char('r')   # reset press
            self._send_char('z')   # dừng cắt
            self._send_char('g')   # đóng cửa
            # đánh dấu lỗi
            for b in (1, 2):
                self._stop_flags[b].set()
                self._emit({"type": "state", "bin": b, "state": "error"})
            self._emit({"type": "log", "level": "error", "msg": "EMERGENCY STOP"})
            # auto hồi về ready sau 3.5s
            threading.Timer(3.5, lambda: [
                self._stop_flags[1].clear(), self._stop_flags[2].clear(),
                self._emit({"type": "state", "bin": 1, "state": "ready"}),
                self._emit({"type": "state", "bin": 2, "state": "ready"}),
            ]).start()

        elif c == "set":
            key = cmd.get("key")
            val = cmd.get("value")
            if bin_id in self._params and key in self._params[bin_id]:
                self._params[bin_id][key] = val
                self._emit({"type": "log", "level": "info",
                            "msg": f"BIN-0{bin_id} · {key} = {val}"})

        elif c == "set_mode" and bin_id == 2:
            self._params[2]["mode"] = cmd.get("mode", "manual")
            self._emit({"type": "mode", "bin": 2, "mode": self._params[2]["mode"]})

    # ---------- helper ----------
    def _is_running(self, bin_id):
        t = self._cycle_threads.get(bin_id)
        return t and t.is_alive()

    def _clear_milestones(self):
        for e in (self._evt_press_threshold, self._evt_press_limit,
                  self._evt_cut_done, self._evt_door_open, self._evt_door_close,
                  self._evt_auto_trigger, self._evt_pump_done):
            e.clear()

    def _reset_steps(self, bin_id):
        """Đẩy event step:idle cho cả 3 bước, dùng khi bắt đầu cycle mới hoặc khi reset/abort."""
        if bin_id == 1:
            names = ("press", "return", "spray")
        else:
            names = ("cut", "bio", "door")
        for n in names:
            self._emit({"type": "step", "bin": bin_id, "step": n, "status": "idle"})

    # ---------- BIN 1: ép vô cơ ----------
    def _start_bin1(self):
        # nếu đang chạy thì bỏ qua, tránh đụng nhau
        if self._is_running(1):
            return
        self._stop_flags[1].clear()
        self._clear_milestones()
        # reset 3 step về idle để UI thấy mới bắt đầu
        self._reset_steps(1)

        t = threading.Thread(target=self._run_bin1_cycle, daemon=True, name="bin1-cycle")
        self._cycle_threads[1] = t
        t.start()

    def _stop_bin1(self):
        """STOP ngăn vô cơ — gửi 'r' cho Mega để dừng motor ép, đưa state về ready."""
        self._stop_flags[1].set()
        self._send_char('r')
        self._reset_steps(1)
        self._emit({"type": "state", "bin": 1, "state": "ready"})

    def _run_bin1_cycle(self):
        """
        Mega tự lo chu kỳ ép, mình chỉ xếp event ra UI theo text Mega trả về:
            's' → "Bat dau chu ky ep" → "DAT NGUONG" → "Da cham cong tac" → done
        Sau khi Mega xong phần ép, mình fake step "spray" vì Mega chưa có bơm phun nước.
        """
        stop = self._stop_flags[1]
        self._emit({"type": "state", "bin": 1, "state": "running"})
        self._emit({"type": "log", "level": "info", "msg": "Bắt đầu chu kỳ ép",
                    "source": "BIN-01"})

        # gửi lệnh
        self._send_char('s')

        # step 1: press — bắt đầu ngay, kết thúc khi parser thấy "DAT NGUONG"
        self._emit({"type": "step", "bin": 1, "step": "press", "status": "active"})
        if not self._wait_milestone(self._evt_press_threshold, stop, timeout=120):
            if stop.is_set():
                return self._abort_bin1()
            self._emit({"type": "log", "level": "warn",
                        "msg": "Timeout chờ Mega đạt ngưỡng ép", "source": "BIN-01"})
        self._emit({"type": "step", "bin": 1, "step": "press", "status": "done"})

        # step 2: return — chạy tới khi limit switch chạm
        self._emit({"type": "step", "bin": 1, "step": "return", "status": "active"})
        if not self._wait_milestone(self._evt_press_limit, stop, timeout=120):
            if stop.is_set():
                return self._abort_bin1()
            self._emit({"type": "log", "level": "warn",
                        "msg": "Timeout chờ Mega hồi vị", "source": "BIN-01"})
        self._emit({"type": "step", "bin": 1, "step": "return", "status": "done"})

        # step 3: spray — gửi 't', chờ Mega gửi "DUNG BOM" khi bơm xong (~3s)
        # UI hiển thị step spray active đúng bằng thời gian bơm thật.
        self._emit({"type": "step", "bin": 1, "step": "spray", "status": "active"})
        self._evt_pump_done.clear()
        self._send_char('t')
        # Chờ pump done thật (Mega 3s) hoặc spray_seconds setting — lấy max + buffer
        pump_timeout = max(self._params[1]["spray_seconds"], 6.0)
        self._wait_milestone(self._evt_pump_done, stop, timeout=pump_timeout)
        self._emit({"type": "step", "bin": 1, "step": "spray", "status": "done"})

        if stop.is_set():
            return self._abort_bin1()

        # Gửi 'r' để Mega reset pressState về IDLE — tránh kẹt ở PRESS_DONE
        # (nếu để nguyên, Mega có khi vẫn bận state này, ảnh hưởng auto bin 2)
        self._send_char('r')

        # xong
        self._emit({"type": "state", "bin": 1, "state": "done"})
        self._emit({"type": "log", "level": "success", "msg": "Chu kỳ ép hoàn tất",
                    "source": "BIN-01"})
        time.sleep(2.0)
        self._emit({"type": "state", "bin": 1, "state": "ready"})

    def _abort_bin1(self):
        self._reset_steps(1)
        self._emit({"type": "sensor", "bin": 1, "force": 0.0, "pos": "home", "pump": "off"})
        self._emit({"type": "state", "bin": 1, "state": "ready"})
        self._emit({"type": "log", "level": "warn", "msg": "Chu kỳ ép bị dừng",
                    "source": "BIN-01"})

    # ---------- BIN 2: hữu cơ (cắt → vi sinh → cửa lật) ----------
    def _start_bin2(self):
        if self._is_running(2):
            return
        self._stop_flags[2].clear()
        self._clear_milestones()
        self._reset_steps(2)

        # Chọn nhánh theo mode
        mode = self._params[2].get("mode", "manual")
        if mode == "auto":
            # ARM auto: gửi 'A' cho Mega, spawn thread chờ AUTO_TRIGGER vô hạn
            t = threading.Thread(target=self._run_bin2_auto, daemon=True, name="bin2-auto")
        else:
            # Manual: chạy 1 chu kỳ ngay
            t = threading.Thread(target=self._run_bin2_cycle, daemon=True, name="bin2-cycle")
        self._cycle_threads[2] = t
        t.start()

    def _stop_bin2(self):
        """
        STOP ngăn hữu cơ — hành vi cũ (file Mega TuDong không có 'D'/'H'):
        - Auto mode đang armed: chỉ abort sub-cycle, giữ auto chờ trigger tiếp.
        - Manual hoặc auto đã off: full stop, gửi 'M' + 'g' cho Mega, về ready.
        """
        mode = self._params[2].get("mode", "manual")
        self._send_char('z')   # dừng motor cắt
        self._send_char('q')   # dừng cả 2 bơm

        if mode == "auto" and self._auto_armed_bin2 and self._is_running(2):
            # Auto đang chạy → abort sub-cycle, giữ auto armed
            self._evt_subcycle_abort.set()
            self._emit({"type": "log", "level": "warn",
                        "msg": "Stop trong Auto — dừng chu kỳ, vẫn chờ trigger tiếp",
                        "source": "BIN-02"})
        else:
            # Full stop
            self._stop_flags[2].set()
            self._send_char('M')   # tắt auto bên Mega
            self._send_char('g')   # đóng cửa nếu đang mở
            self._auto_armed_bin2 = False
            self._reset_steps(2)
            self._emit({"type": "sensor", "bin": 2, "rpm": 0, "sense": "clear",
                        "bio": "off", "door": "closed"})
            self._emit({"type": "state", "bin": 2, "state": "ready"})

    def _run_bin2_cycle(self):
        stop = self._stop_flags[2]
        self._emit({"type": "state", "bin": 2, "state": "running"})
        self._emit({"type": "log", "level": "info", "msg": "Bắt đầu chu kỳ hữu cơ",
                    "source": "BIN-02"})

        # ===== step 1: cut =====
        # Mega chỉ chấp nhận 1, 3, 5 phút → snap về giá trị gần nhất
        wanted = self._params[2]["cut_minutes"]
        valid = min((1, 3, 5), key=lambda x: abs(x - wanted))
        self._send_char(str(valid))    # set thời gian
        time.sleep(0.15)
        self._send_char('y')           # start cắt
        self._emit({"type": "step", "bin": 2, "step": "cut", "status": "active"})

        # Mega sẽ tự dừng khi hết thời gian, mình chờ event "DUNG MOTOR CAT"
        # timeout = thời gian cắt + buffer 5s
        timeout_s = valid * 60 + 5
        if not self._wait_milestone(self._evt_cut_done, stop, timeout=timeout_s):
            if stop.is_set():
                self._send_char('z')
                return self._abort_bin2()
            self._emit({"type": "log", "level": "warn",
                        "msg": "Timeout chờ Mega dừng motor cắt", "source": "BIN-02"})
            self._send_char('z')
        self._emit({"type": "step", "bin": 2, "step": "cut", "status": "done"})

        # ===== step 2: bio — gửi 'v' (bơm vi sinh thật), chờ "DUNG BOM" =====
        # Đảm bảo bơm xong rồi mới qua step door
        self._emit({"type": "step", "bin": 2, "step": "bio", "status": "active"})
        self._emit({"type": "sensor", "bin": 2, "bio": "on"})
        self._evt_pump_done.clear()
        self._send_char('v')
        pump_timeout = max(self._params[2]["bio_seconds"], 6.0)
        self._wait_milestone(self._evt_pump_done, stop, timeout=pump_timeout)
        self._emit({"type": "sensor", "bin": 2, "bio": "off"})
        if stop.is_set():
            return self._abort_bin2()
        self._emit({"type": "step", "bin": 2, "step": "bio", "status": "done"})

        # ===== step 3: door — mở rồi đóng lại =====
        self._emit({"type": "step", "bin": 2, "step": "door", "status": "active"})

        self._send_char('x')           # mở cửa
        if not self._wait_milestone(self._evt_door_open, stop, timeout=20):
            if stop.is_set(): return self._abort_bin2()
            self._emit({"type": "log", "level": "warn",
                        "msg": "Timeout chờ cửa mở", "source": "BIN-02"})

        # giữ cửa mở (poll stop để bấm STOP có hiệu lực ngay)
        if stop.wait(timeout=self._params[2]["door_seconds"]):
            return self._abort_bin2()

        self._send_char('g')           # đóng cửa
        if not self._wait_milestone(self._evt_door_close, stop, timeout=20):
            if stop.is_set(): return self._abort_bin2()
            self._emit({"type": "log", "level": "warn",
                        "msg": "Timeout chờ cửa đóng", "source": "BIN-02"})

        self._emit({"type": "step", "bin": 2, "step": "door", "status": "done"})

        # xong
        self._emit({"type": "state", "bin": 2, "state": "done"})
        self._emit({"type": "log", "level": "success", "msg": "Chu kỳ hữu cơ hoàn tất",
                    "source": "BIN-02"})
        time.sleep(2.0)
        self._emit({"type": "state", "bin": 2, "state": "ready"})

    def _abort_bin2(self):
        self._reset_steps(2)
        self._emit({"type": "sensor", "bin": 2, "rpm": 0, "sense": "clear",
                    "bio": "off", "door": "closed"})
        self._emit({"type": "state", "bin": 2, "state": "ready"})
        self._emit({"type": "log", "level": "warn", "msg": "Chu kỳ hữu cơ bị dừng",
                    "source": "BIN-02"})

    # ---------- BIN 2 AUTO MODE ----------
    def _run_bin2_auto(self):
        """
        Auto loop: bật 'A' cho Mega → chờ AUTO_TRIGGER → chạy sub-cycle → quay lại chờ.
        Thoát khi _stop_flags[2] (full disarm) — STOP trong auto chỉ abort sub-cycle.

        Badge UI:
          - Khi armed mà chưa có cycle: state=ready (giống manual idle)
          - Khi đang chạy sub-cycle: state=running
          - Sau cycle xong: lại ready chờ trigger tiếp
        """
        stop = self._stop_flags[2]
        abort = self._evt_subcycle_abort
        self._auto_armed_bin2 = True
        self._send_char('A')   # bật auto bên Mega
        # Armed nhưng chưa có cycle → state=ready (chứ không phải running)
        self._emit({"type": "state", "bin": 2, "state": "ready"})
        self._emit({"type": "log", "level": "info",
                    "msg": "Auto mode ARMED — chờ cảm biến phát hiện rác 5s",
                    "source": "BIN-02"})

        while not stop.is_set():
            # Reset 2 cờ trước mỗi vòng để sạch state
            abort.clear()
            self._evt_auto_trigger.clear()

            # Chờ AUTO_TRIGGER từ Mega (sensor 5s detected) — vẫn ở Ready trong lúc chờ
            if not self._wait_milestone(self._evt_auto_trigger, stop, timeout=3600):
                if stop.is_set():
                    break
                continue   # timeout 1h, loop tiếp

            if stop.is_set():
                break

            # Có trigger → state=running, chạy 1 chu kỳ (cut → bio → door)
            self._emit({"type": "state", "bin": 2, "state": "running"})
            self._emit({"type": "log", "level": "success",
                        "msg": "AUTO_TRIGGER → bắt đầu chu kỳ", "source": "BIN-02"})
            self._reset_steps(2)
            self._run_bin2_subcycle(stop)

            # Nếu sub-cycle bị abort bởi STOP → cleanup state, vẫn armed
            if abort.is_set() and not stop.is_set():
                time.sleep(0.3)
                self._send_char('g')   # đảm bảo cửa đóng (Mega bỏ qua nếu đã đóng)
                self._reset_steps(2)
                self._emit({"type": "sensor", "bin": 2, "rpm": 0, "sense": "clear",
                            "bio": "off", "door": "closed"})
                self._emit({"type": "state", "bin": 2, "state": "ready"})   # về ready chờ trigger
                self._emit({"type": "log", "level": "warn",
                            "msg": "Chu kỳ dừng — Auto vẫn armed, chờ trigger tiếp",
                            "source": "BIN-02"})
            else:
                # Chu kỳ hoàn tất bình thường → về Ready chờ trigger lần sau
                self._emit({"type": "state", "bin": 2, "state": "ready"})
                self._emit({"type": "log", "level": "info",
                            "msg": "Chờ trigger lần tiếp theo (cách tối thiểu 30s)",
                            "source": "BIN-02"})

        # Outer stop (Manual toggle hoặc full disarm) → tắt auto bên Mega
        self._auto_armed_bin2 = False
        self._send_char('M')
        self._reset_steps(2)
        self._emit({"type": "state", "bin": 2, "state": "ready"})
        self._emit({"type": "log", "level": "info", "msg": "Auto mode tắt", "source": "BIN-02"})

    def _run_bin2_subcycle(self, stop):
        """
        1 chu kỳ con auto: cắt → phun vi sinh → mở cửa 5s → đóng cửa.
        Tất cả wait/sleep đều check both stop_flag (full disarm) và subcycle_abort (STOP nhưng giữ auto).
        Khi abort: thoát ngay không cleanup ở đây — outer loop sẽ cleanup state.
        """
        abort = self._evt_subcycle_abort

        # ===== Step 1: cut =====
        wanted = self._params[2]["cut_minutes"]
        valid = min((1, 3, 5), key=lambda x: abs(x - wanted))
        self._clear_milestones()
        self._send_char(str(valid))
        time.sleep(0.15)
        self._send_char('y')
        self._emit({"type": "step", "bin": 2, "step": "cut", "status": "active"})

        timeout_s = valid * 60 + 5
        if not self._wait_milestone(self._evt_cut_done, stop, timeout=timeout_s, also_abort=abort):
            self._send_char('z')   # dừng cắt khi abort hoặc timeout
            if stop.is_set() or abort.is_set():
                return
        self._emit({"type": "step", "bin": 2, "step": "cut", "status": "done"})

        # ===== Step 2: phun vi sinh — gửi 'v', CHỜ "DUNG BOM" thật rồi mới qua step door =====
        # Đảm bảo thứ tự: cut → pump xong → door (không trùng lặp)
        self._emit({"type": "step", "bin": 2, "step": "bio", "status": "active"})
        self._emit({"type": "sensor", "bin": 2, "bio": "on"})
        self._evt_pump_done.clear()
        self._send_char('v')
        pump_timeout = max(self._params[2]["bio_seconds"], 6.0)
        ok = self._wait_milestone(self._evt_pump_done, stop, timeout=pump_timeout, also_abort=abort)
        self._emit({"type": "sensor", "bin": 2, "bio": "off"})
        if not ok and (stop.is_set() or abort.is_set()):
            return
        self._emit({"type": "step", "bin": 2, "step": "bio", "status": "done"})

        # ===== Step 3: door — mở 5s rồi đóng =====
        self._emit({"type": "step", "bin": 2, "step": "door", "status": "active"})
        self._send_char('x')
        if not self._wait_milestone(self._evt_door_open, stop, timeout=20, also_abort=abort):
            if stop.is_set() or abort.is_set(): return

        if self._interruptible_sleep(5.0, stop, also_abort=abort):
            return

        self._send_char('g')
        if not self._wait_milestone(self._evt_door_close, stop, timeout=20, also_abort=abort):
            if stop.is_set() or abort.is_set(): return

        self._emit({"type": "step", "bin": 2, "step": "door", "status": "done"})

    # ---------- parser thread ----------
    def _loop(self):
        """
        Đọc serial liên tục, parse text Mega để:
          - Đẩy lực ép realtime lên UI (Luc nen: X g)
          - Bật event milestone cho orchestrator (DAT NGUONG, DUNG MOTOR CAT, ...)
          - Cập nhật mức dung dịch (LEVEL_DETERGENT_*, LEVEL_MICROBE_*)
        """
        buf = b""
        while not self._stop.is_set():
            if not self.connected:
                if not self._open():
                    time.sleep(config.SERIAL_RECONNECT_S)
                    continue
            try:
                chunk = self._ser.read(256)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip().decode("utf-8", "ignore")
                    if not line:
                        continue
                    self._parse_line(line)
            except Exception as e:
                log.error("UART read fail: %s", e)
                self._close()
                self._emit({"type": "log", "level": "warn",
                            "msg": f"Mất kết nối Mega {self.port}, thử lại..."})
                time.sleep(config.SERIAL_RECONNECT_S)

    def _parse_line(self, line: str):
        """
        Phân loại dòng text từ Mega.
        Lưu ý quan trọng: Mega in NHIỀU thông tin trên cùng 1 dòng, ví dụ:
          "Luc nen: 1500.0 g | DAT NGUONG -> DUNG EP."
        nên KHÔNG được return sớm sau khi match force — phải check hết milestone.
        """
        if len(line) < 100:
            log.debug("← Mega: %s", line)

        matched_specific = False

        # ===== Lực ép (realtime, ~3 Hz khi đang ép) =====
        m = self.RE_FORCE.search(line)
        if m:
            try:
                force_g = float(m.group(1))
                # Mega trả gram, HMI gauge dùng kgf → chia 1000
                self._emit({"type": "sensor", "bin": 1,
                            "force": round(force_g / 1000.0, 2),
                            "pos": "extend"})
            except ValueError:
                pass
            matched_specific = True
            # KHÔNG return — cùng dòng còn có thể có "DAT NGUONG"

        # ===== Mức dung dịch =====
        m = self.RE_LEVEL_DET.search(line)
        if m:
            val = {"OK": 100, "LOW": 10, "CHECKING": 50}[m.group(1)]
            self._emit({"type": "sysinfo", "water": val})
            matched_specific = True

        m = self.RE_LEVEL_MIC.search(line)
        if m:
            val = {"OK": 100, "LOW": 10, "CHECKING": 50}[m.group(1)]
            self._emit({"type": "sysinfo", "bio_level": val})
            matched_specific = True

        # ===== Cam bien JSN-SR04T bao day thung rac (Mega moi) =====
        # FULL    -> rac da day      -> UI hien badge "HIGH"  (do)
        # OK      -> chua day        -> UI hien badge "LOW"   (xanh)
        # CHECKING-> chua co du lieu -> UI hien "?"
        m = self.RE_BIN_FILL.search(line)
        if m:
            kind = m.group(1)         # INORGANIC / ORGANIC
            state = m.group(2)        # FULL / OK / CHECKING
            bin_id = 1 if kind == "INORGANIC" else 2
            level_map = {"FULL": "high", "OK": "low", "CHECKING": "checking"}
            level_label = {"FULL": "Đầy", "OK": "Chưa đầy", "CHECKING": "Đang đo"}
            self._emit({
                "type": "fill", "bin": bin_id,
                "state": level_map[state],     # high | low | checking
                "label": level_label[state],
            })
            if state == "FULL":
                self._emit({"type": "log", "level": "warn",
                            "msg": f"Thùng rác {'vô cơ' if bin_id == 1 else 'hữu cơ'} ĐÃ ĐẦY — cần đổ rác",
                            "source": f"BIN-0{bin_id}"})
            matched_specific = True

        # ===== Bin 1 — chu kỳ ép =====
        if "DAT NGUONG" in line:
            self._evt_press_threshold.set()
            self._emit({"type": "sensor", "bin": 1, "pos": "retract"})
            self._emit({"type": "log", "level": "info",
                        "msg": "Đạt ngưỡng ép → bắt đầu hồi vị", "source": "BIN-01"})
            matched_specific = True
        if "Bat dau quay nguoc" in line:
            self._emit({"type": "sensor", "bin": 1, "pos": "retract"})
            matched_specific = True
        if "DA CHAM CONG TAC HANH TRINH" in line:
            self._evt_press_limit.set()
            self._emit({"type": "sensor", "bin": 1, "pos": "home", "force": 0.0})
            self._emit({"type": "log", "level": "info",
                        "msg": "Chạm công tắc hành trình", "source": "BIN-01"})
            matched_specific = True

        # ===== Bin 2 — cắt =====
        if "BAT DAU CAT" in line:
            self._emit({"type": "sensor", "bin": 2, "rpm": 1200,
                        "sense": "object", "bio": "off"})
            matched_specific = True
        if "DUNG MOTOR CAT" in line:
            self._evt_cut_done.set()
            self._emit({"type": "sensor", "bin": 2, "rpm": 0, "sense": "clear"})
            matched_specific = True

        # ===== Bơm phun (chung cho cả bin 1 'detergent' và bin 2 'microbe') =====
        # Mega in "DUNG BOM - <reason>" khi bơm tắt (do hết thời gian hoặc lệnh q).
        if "DUNG BOM" in line:
            self._evt_pump_done.set()
            matched_specific = True

        # ===== Bin 2 — cửa =====
        if "Da mo cua 90" in line:
            self._evt_door_open.set()
            self._emit({"type": "sensor", "bin": 2, "door": "open"})
            matched_specific = True
        if "Da dong cua" in line:
            self._evt_door_close.set()
            self._emit({"type": "sensor", "bin": 2, "door": "closed"})
            matched_specific = True

        # ===== Cảm biến E18 phát hiện rác =====
        # ⚠ Dùng EXACT match (line == "...") chứ không phải `in`,
        # vì banner setup có thể chứa các từ này trong câu mô tả → fire nhầm.
        stripped = line.strip()
        if stripped == "SENSOR_DETECT":
            self._emit({"type": "sensor", "bin": 2, "sense": "detect"})
            matched_specific = True
        elif stripped == "SENSOR_CLEAR":
            self._emit({"type": "sensor", "bin": 2, "sense": "clear"})
            matched_specific = True
        # giữ lại message cũ để tương thích (Mega cũ chưa upload)
        elif "Cam bien dang phat hien vat" in line:
            self._emit({"type": "sensor", "bin": 2, "sense": "detect"})
            matched_specific = True
        elif "Phat hien vat lien tuc 5s" in line:
            self._emit({"type": "log", "level": "info",
                        "msg": "Cảm biến phát hiện rác 5s → tự mở cửa", "source": "BIN-02"})
            matched_specific = True

        # ===== AUTO MODE (Mega phiên bản mới) — EXACT match =====
        if stripped == "AUTO_TRIGGER":
            self._evt_auto_trigger.set()
            self._emit({"type": "log", "level": "success",
                        "msg": "AUTO_TRIGGER nhận từ Mega", "source": "BIN-02"})
            matched_specific = True
        elif stripped == "AUTO_MODE_ON":
            self._emit({"type": "log", "level": "info",
                        "msg": "Mega xác nhận: auto mode ON", "source": "BIN-02"})
            matched_specific = True
        elif stripped == "AUTO_MODE_OFF":
            self._emit({"type": "log", "level": "info",
                        "msg": "Mega xác nhận: auto mode OFF", "source": "BIN-02"})
            matched_specific = True

        # ===== còn lại — gửi nguyên text vào log để debug =====
        if matched_specific:
            return
        if line.startswith("===") or "----" in line:
            return    # bỏ banner khởi động, không spam log UI
        # Mega cũ (chưa upload .ino mới) có thể spam "DONE phan ep..." mỗi 1s.
        # Lọc luôn để khỏi flood Nhật ký.
        if "DONE phan ep" in line:
            return
        self._emit({"type": "log", "level": "info", "msg": f"MCU: {line}",
                    "source": "MEGA"})

    def _wait_milestone(self, event: threading.Event, stop_flag: threading.Event,
                        timeout: float, also_abort: threading.Event = None) -> bool:
        """
        Chờ event milestone HOẶC stop flag (HOẶC abort flag nếu có) — tránh wait long block.
        Poll mỗi 0.3s — đủ nhanh cho UI mà không ngốn CPU.
        Trả True nếu milestone tới, False nếu stop / abort / timeout.
        also_abort dùng cho sub-cycle trong auto mode (xem _evt_subcycle_abort).
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_flag.is_set():
                return False
            if also_abort is not None and also_abort.is_set():
                return False
            if event.wait(timeout=0.3):
                return True
        return False

    def _interruptible_sleep(self, timeout: float, stop_flag: threading.Event,
                              also_abort: threading.Event = None) -> bool:
        """
        Ngủ N giây, nhưng tỉnh sớm nếu stop hoặc abort được bật.
        Trả True nếu bị ngắt giữa chừng, False nếu ngủ đủ.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if stop_flag.is_set():
                return True
            if also_abort is not None and also_abort.is_set():
                return True
            time.sleep(min(0.2, max(0.01, deadline - time.time())))
        return False


# =============================================================
#  MOCK HARDWARE — chạy khi không có Mega để test UI
# =============================================================
class MockHardware(_Base):
    """Giả lập đầy đủ chu trình cho cả 2 ngăn. Cho HMI chạy đẹp khi chưa nối Mega."""
    mode = "mock"

    def __init__(self, on_event):
        super().__init__(on_event)
        self.connected = True
        self._cycle_threads = {}
        self._stop_flags = {1: threading.Event(), 2: threading.Event()}
        self._params = {
            1: {"press_threshold_pct": 60, "spray_seconds": 3},
            2: {"cut_minutes": 1, "bio_seconds": 2, "door_seconds": 4, "mode": "manual"},
        }
        self._mode2 = "manual"

    def _loop(self):
        """Mock không cần background loop — chu kỳ chạy trong _run_cycle khi user start."""
        # Sleep cho đến khi stop, không spam CPU
        while not self._stop.is_set():
            self._stop.wait(timeout=1.0)

    def send(self, cmd: dict):
        c = cmd.get("cmd")
        bin_id = cmd.get("bin")
        if c == "start":
            self._start_cycle(bin_id)
        elif c == "stop":
            self._stop_cycle(bin_id)
        elif c == "reset":
            self._stop_cycle(bin_id)
            self._emit({"type": "state", "bin": bin_id, "state": "ready"})
        elif c == "estop":
            for b in (1, 2):
                self._stop_cycle(b)
                self._emit({"type": "state", "bin": b, "state": "error"})
            self._emit({"type": "log", "level": "error", "msg": "EMERGENCY STOP"})
            threading.Timer(3.5, lambda: [
                self._emit({"type": "state", "bin": 1, "state": "ready"}),
                self._emit({"type": "state", "bin": 2, "state": "ready"}),
            ]).start()
        elif c == "set":
            self._handle_set(cmd)
        elif c == "set_mode" and bin_id == 2:
            self._mode2 = cmd.get("mode", "manual")
            self._emit({"type": "mode", "bin": 2, "mode": self._mode2})

    def _handle_set(self, cmd):
        bin_id = cmd.get("bin"); key = cmd.get("key"); val = cmd.get("value")
        if bin_id in self._params and key in self._params[bin_id]:
            self._params[bin_id][key] = val


    def _start_cycle(self, bin_id):
        if bin_id not in (1, 2): return
        if self._cycle_threads.get(bin_id) and self._cycle_threads[bin_id].is_alive(): return
        self._stop_flags[bin_id].clear()
        t = threading.Thread(target=self._run_cycle, args=(bin_id,), daemon=True)
        self._cycle_threads[bin_id] = t
        t.start()

    def _stop_cycle(self, bin_id):
        if bin_id in self._stop_flags:
            self._stop_flags[bin_id].set()

    def _reset_steps_mock(self, bin_id):
        names = ("press","return","spray") if bin_id == 1 else ("cut","bio","door")
        for n in names:
            self._emit({"type":"step","bin":bin_id,"step":n,"status":"idle"})

    def _run_cycle(self, bin_id):
        """Mô phỏng chu kỳ — chỉ phát event theo lịch để UI hoạt động khi không có Mega."""
        stop = self._stop_flags[bin_id]
        self._reset_steps_mock(bin_id)
        self._emit({"type": "state", "bin": bin_id, "state": "running"})

        if bin_id == 1:
            steps = [("press", 4, "Ép nén rác"),
                     ("return", 3, "Hồi vị"),
                     ("spray", 3, "Phun nước rửa")]
        else:
            steps = [("cut", 8, "Cắt rác hữu cơ"),
                     ("bio", 2, "Phun vi sinh"),
                     ("door", 5, "Mở/đóng cửa lật")]

        for name, dur, msg in steps:
            if stop.is_set():
                break
            self._emit({"type": "step", "bin": bin_id, "step": name, "status": "active"})
            self._emit({"type": "log", "level": "info", "msg": msg,
                        "source": f"BIN-0{bin_id}"})
            # phát realtime sensor giả lập
            t0 = time.time()
            while time.time() - t0 < dur:
                if stop.is_set():
                    break
                if bin_id == 1 and name == "press":
                    self._emit({"type": "sensor", "bin": 1,
                                "force": round(random.uniform(8.0, 14.0), 2),
                                "pos": "extend"})
                elif bin_id == 2 and name == "cut":
                    self._emit({"type": "sensor", "bin": 2,
                                "rpm": random.randint(1400, 1800),
                                "sense": "detect" if random.random() < 0.3 else "clear"})
                time.sleep(0.5)
            if not stop.is_set():
                self._emit({"type": "step", "bin": bin_id, "step": name, "status": "done"})

        if stop.is_set():
            self._reset_steps_mock(bin_id)
            self._emit({"type": "state", "bin": bin_id, "state": "ready"})
            self._emit({"type": "log", "level": "warn",
                        "msg": "Chu ky bi dung (mock)", "source": f"BIN-0{bin_id}"})
            return

        self._emit({"type": "state", "bin": bin_id, "state": "done"})
        self._emit({"type": "log", "level": "success",
                    "msg": "Chu ky hoan tat (mock)", "source": f"BIN-0{bin_id}"})
        time.sleep(2.0)
        self._emit({"type": "state", "bin": bin_id, "state": "ready"})


# =============================================================
#  FACTORY
# =============================================================
def make_hardware(on_event: Callable[[dict], None]) -> _Base:
    if config.FORCE_MOCK or not HAS_SERIAL:
        if not HAS_SERIAL:
            log.warning("pyserial khong co -> MockHardware")
            on_event({"type": "log", "level": "error",
                      "msg": "Thiếu thư viện pyserial -> chạy MOCK (máy thật không chạy). Cài:  pip install pyserial"})
        else:
            log.info("XBIN_MOCK=1 -> force MockHardware")
            on_event({"type": "log", "level": "warn",
                      "msg": "XBIN_MOCK=1 -> đang ép chạy MOCK (máy thật không chạy)."})
        return MockHardware(on_event)

    ports = []
    if config.SERIAL_PORT_OVERRIDE:
        ports.append(config.SERIAL_PORT_OVERRIDE)
    ports.extend(p for p in config.SERIAL_PORTS if p not in ports)

    for port in ports:
        try:
            hw = MegaHardware(on_event, port=port, baudrate=config.SERIAL_BAUDRATE)
            if hw._open():
                hw._close()
                log.info("Se dung MegaHardware tren %s", port)
                return hw
        except Exception as e:
            log.debug("Thu port %s fail: %s", port, e)

    log.warning("Khong mo duoc serial nao -> fallback MockHardware")
    on_event({"type": "log", "level": "error",
              "msg": ("KHÔNG mở được cổng serial nào (" + ", ".join(ports) + ") -> chạy MOCK. "
                      "Máy thật sẽ KHÔNG hoạt động. Chạy ./check_serial.sh trên Q509 để biết lý do.")})
    return MockHardware(on_event)
