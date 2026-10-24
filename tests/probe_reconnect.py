"""Clean-link dance: release the ACL entirely, then probe SDP + channel 2.

Sequence:
  1. bluetoothctl disconnect <addr>  (buds idle, ACL down)
  2. fresh SDP query (this itself brings up a new ACL)
  3. bluetoothctl connect <addr>     (audio back)
  4. patient RFCOMM probe on channel 2 (and fallbacks)
"""
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bp1pro import sdp  # noqa: E402

ADDR = sys.argv[1] if len(sys.argv) > 1 else "44:38:EB:xx:xx:xx"


def ctl(*args: str) -> str:
    out = subprocess.run(["bluetoothctl", *args], capture_output=True,
                         text=True, timeout=25, check=False)
    return (out.stdout + out.stderr).replace("\x1b", "").strip()[:90]


def rfcomm_probe(channel: int, connect_timeout: float = 20.0,
                 window: float = 3.0) -> str:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                         socket.BTPROTO_RFCOMM)
    sock.settimeout(connect_timeout)
    t0 = time.monotonic()
    try:
        sock.connect((ADDR, channel))
    except OSError as exc:
        sock.close()
        return (f"{type(exc).__name__} errno={exc.errno} "
                f"after {time.monotonic() - t0:.1f}s")
    try:
        sock.send(bytes.fromhex("BA33"))
        rx = bytearray()
        end = time.time() + window
        sock.settimeout(0.5)
        while time.time() < end:
            try:
                data = sock.recv(256)
                if not data:
                    break
                rx.extend(data)
            except socket.timeout:
                pass
        sock.close()
        if rx:
            return (f"LINKED {time.monotonic() - t0:.1f}s — "
                    f"reply {bytes(rx).hex().upper()}")
        return f"linked {time.monotonic() - t0:.1f}s, silent to BA33"
    except OSError as exc:
        return f"linked, io error {exc}"


print("[1] disconnect:", ctl("disconnect", ADDR))
time.sleep(4)

print("[2] SDP query from idle:", sdp.query_spp_channel(ADDR, timeout=4.0))
time.sleep(1)

print("[3] connect:", ctl("connect", ADDR))
time.sleep(2)

for channel in (2, 1, 3, 8):
    print(f"[4] channel {channel}:", rfcomm_probe(channel), flush=True)
