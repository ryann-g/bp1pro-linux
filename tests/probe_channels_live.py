"""Live RFCOMM scan against real earbuds — diagnostics for channel discovery."""
import socket
import sys
import time

ADDR = sys.argv[1] if len(sys.argv) > 1 else "44:38:EB:xx:xx:xx"
CHANNELS = range(1, 31)


def probe(channel: int) -> str:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                         socket.BTPROTO_RFCOMM)
    try:
        sock.settimeout(3.0)
        sock.connect((ADDR, channel))
    except OSError as exc:
        return f"ref errno={exc.errno} ({exc.strerror})"
    try:
        sock.send(bytes.fromhex("BA33"))
        rx = bytearray()
        window = time.time() + 2.5
        sock.settimeout(0.5)
        while time.time() < window:
            try:
                data = sock.recv(256)
                if not data:
                    break
                rx.extend(data)
            except socket.timeout:
                pass
        sock.close()
        if rx:
            return f"OK idle answer: {bytes(rx).hex().upper()}"
        return "OK but silent to BA33"
    except OSError as exc:
        try:
            sock.close()
        except OSError:
            pass
        return f"CONNECTED but send/recv failed: {exc}"


for ch in CHANNELS:
    print(f"channel {ch:2d}: {probe(ch)}", flush=True)
