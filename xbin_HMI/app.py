"""
X-BIN · Flask + Flask-SocketIO backend
Chạy:   python3 app.py
Kiosk:  http://localhost:5000
"""
# QUAN TRỌNG: monkey_patch PHẢI là thứ đầu tiên — trước mọi import khác —
# để threading.Thread (trong hardware.py) trở thành greenlet và có thể
# emit qua SocketIO ổn định.
import eventlet
eventlet.monkey_patch()

import logging
import os
import time
import sys
from threading import Lock

from flask import Flask, send_from_directory, jsonify, request
from flask_socketio import SocketIO, emit

import config
import hardware

# ----- logging -----
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("xbin.app")

# ----- Flask -----
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.config["SECRET_KEY"] = config.SECRET_KEY

# async_mode="eventlet" + monkey_patch ở đầu → production-ready,
# non-blocking I/O, hardware.py spawn greenlet, emit OK trên mọi client.
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

# ----- state cache (mới connect thì gửi snapshot) -----
state_lock = Lock()
state_cache = {
    "bin1": {"state": "ready", "progress": 0,
             "sensor": {"force": 0.0, "pos": "home", "pump": "off"}},
    "bin2": {"state": "ready", "progress": 0,
             "sensor": {"rpm": 0, "sense": "clear", "bio": "off", "door": "closed"},
             "mode": "manual"},
    "sysinfo": {"temp": 28.0, "cpu": 47.0, "water": 88, "bio_level": 72},
    "cycles": 0,
    "params": {"bin1": dict(config.DEFAULTS["bin1"]),
               "bin2": dict(config.DEFAULTS["bin2"]),
               "system": dict(config.DEFAULTS["system"])},
    "hw_mode": "?",
    "started_at": time.time(),
}
recent_logs = []  # giữ ~80 dòng gần nhất cho client mới


# ----- callback từ hardware → broadcast SocketIO -----
def on_hw_event(evt: dict):
    """Hardware (UART hoặc Mock) sinh event → cập nhật cache + broadcast."""
    et = evt.get("type")
    with state_lock:
        if et == "state":
            b = evt.get("bin")
            if b in (1, 2):
                state_cache[f"bin{b}"]["state"] = evt.get("state", "ready")
                if evt.get("state") == "done":
                    state_cache["cycles"] += 1
                    s = state_cache["sysinfo"]
                    s["water"]     = max(5, s["water"] - 1)
                    s["bio_level"] = max(5, s["bio_level"] - 1)
        elif et == "progress":
            b = evt.get("bin")
            if b in (1, 2):
                state_cache[f"bin{b}"]["progress"] = evt.get("pct", 0)
        elif et == "sensor":
            b = evt.get("bin")
            if b in (1, 2):
                merged = dict(state_cache[f"bin{b}"]["sensor"])
                for k in ("force", "pos", "pump", "rpm", "sense", "bio", "door"):
                    if k in evt:
                        merged[k] = evt[k]
                state_cache[f"bin{b}"]["sensor"] = merged
        elif et == "sysinfo":
            state_cache["sysinfo"].update({k: v for k, v in evt.items() if k != "type"})
        elif et == "mode":
            b = evt.get("bin")
            if b == 2:
                state_cache["bin2"]["mode"] = evt.get("mode", "manual")
        elif et == "log":
            entry = {
                "ts":     time.time(),
                "level":  evt.get("level", "info"),
                "msg":    evt.get("msg", ""),
                "source": evt.get("source", "SYS"),
            }
            recent_logs.append(entry)
            if len(recent_logs) > 80:
                recent_logs.pop(0)

    # Broadcast tới mọi client
    socketio.emit("hw", evt)


# ----- khởi tạo hardware -----
hw = hardware.make_hardware(on_hw_event)
state_cache["hw_mode"] = hw.mode
hw.start()
log.info("Hardware backend: %s (connected=%s)", hw.mode, hw.connected)


# =============================================================
#  HTTP routes
# =============================================================
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/health")
def health():
    return jsonify({
        "ok":         True,
        "hw_mode":    hw.mode,
        "connected":  hw.connected,
        "uptime_s":   int(time.time() - state_cache["started_at"]),
        "cycles":     state_cache["cycles"],
    })


# =============================================================
#  SocketIO events
# =============================================================
@socketio.on("connect")
def on_connect():
    log.info("Client connected: %s", _sid())
    with state_lock:
        emit("snapshot", {
            "hw_mode":    hw.mode,
            "connected":  hw.connected,
            "bin1":       state_cache["bin1"],
            "bin2":       state_cache["bin2"],
            "sysinfo":    state_cache["sysinfo"],
            "cycles":     state_cache["cycles"],
            "params":     state_cache["params"],
            "logs":       recent_logs[-50:],
            "started_at": state_cache["started_at"],
        })


@socketio.on("disconnect")
def on_disconnect():
    log.info("Client disconnected: %s", _sid())


@socketio.on("cmd")
def on_cmd(data):
    """Client gửi lệnh điều khiển từ HMI."""
    action = (data or {}).get("action")
    bin_id = (data or {}).get("bin")

    if action in ("start", "stop", "reset"):
        hw.send({"cmd": action, "bin": bin_id})
        on_hw_event({"type": "log", "level": "info",
                     "msg": f"Lệnh {action.upper()} từ HMI",
                     "source": f"BIN-0{bin_id}" if bin_id else "SYS"})
    elif action == "estop":
        hw.send({"cmd": "estop"})
        on_hw_event({"type": "log", "level": "error",
                     "msg": "EMERGENCY STOP từ HMI", "source": "SYS"})
    elif action == "set":
        key = data.get("key")
        val = data.get("value")
        bkey = f"bin{bin_id}"
        with state_lock:
            if bkey in state_cache["params"] and key in state_cache["params"][bkey]:
                state_cache["params"][bkey][key] = val
        hw.send({"cmd": "set", "bin": bin_id, "key": key, "value": val})
    elif action == "set_mode":
        mode = data.get("mode", "manual")
        with state_lock:
            state_cache["params"]["bin2"]["mode"] = mode
            state_cache["bin2"]["mode"] = mode
        hw.send({"cmd": "set_mode", "bin": 2, "mode": mode})
        on_hw_event({"type": "mode", "bin": 2, "mode": mode})
    elif action == "set_option":
        key = data.get("key")
        val = bool(data.get("value"))
        with state_lock:
            state_cache["params"]["system"][key] = val
        on_hw_event({"type": "log", "level": "info",
                     "msg": f"Option {key} = {val}", "source": "SYS"})
    else:
        log.warning("Unknown cmd: %s", data)


def _sid():
    try:
        return request.sid
    except Exception:
        return "?"


# =============================================================
if __name__ == "__main__":
    banner = (
        "=" * 60 + "\n"
        "  X-BIN HMI Backend\n"
        "  Listen:   http://%s:%d\n"
        "  Hardware: %s\n"
        "%s\n"
    ) % (config.HOST, config.PORT, hw.mode, "=" * 60)
    sys.stderr.write(banner)
    sys.stderr.flush()
    socketio.run(
        app,
        host=config.HOST,
        port=config.PORT,
        debug=config.DEBUG,
    )
