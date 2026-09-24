import os, pty, time, threading, sys
import config
# point to a pty
master, slave = pty.openpty()
slave_name = os.ttyname(slave)
config.SERIAL_PORTS = [slave_name]
config.SERIAL_PORT_OVERRIDE = None
config.FORCE_MOCK = False
import hardware

captured = []
def reader():
    while True:
        try:
            b = os.read(master, 256)
            if b: captured.append(b)
        except OSError:
            break
threading.Thread(target=reader, daemon=True).start()

events=[]
hw = hardware.make_hardware(lambda e: events.append(e))
print("HW MODE:", hw.mode, "connected:", hw.connected)
hw.start()
time.sleep(3)   # let _loop reopen
print("after start connected:", hw.connected)

def dump(label):
    time.sleep(1.0)
    data=b"".join(captured); captured.clear()
    print(f"[{label}] bytes sent to Mega: {data!r}")

dump("idle (3s, expect fill-poll 'u')")
print(">>> sending start bin1")
hw.send({"cmd":"start","bin":1}); dump("start bin1 (first ~1s)")
print(">>> sending stop bin1")
hw.send({"cmd":"stop","bin":1}); dump("stop bin1")
print(">>> sending start bin2 (manual)")
hw.send({"cmd":"start","bin":2}); dump("start bin2")
hw.send({"cmd":"stop","bin":2}); dump("stop bin2")
print("DONE")
os._exit(0)
