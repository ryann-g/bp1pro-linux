"""Patient RFCOMM scan: give each channel a long negotiation window."""
import socket
import sys
import time

ADDR = sys.argv[1] if len(sys.argv) > 1 else "44:38:EB:xx:xx:xx"
CONNECT_TIMEOUT = 12.0
PROBE_WINDOW = 3.0


def probe(channel: int) -> str:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                         socket.BTPROTO_RFCOMM)
    try:
        sock.settimeout(CONNECT_TIMEOUT)
        t0 = time.monotonic()
        sock.connect((ADDR, channel))
        took = time.monotonic() - t0
    except OSError as exc:
        return f"no ({type(exc).__name__} errno={exc.errno} after {exc})"
    try:
        sock.send(bytes.fromhex("BA33"))
        rx = bytearray()
        window = time.time() + PROBE_WINDOW
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
            return f"YES after {took:.1f}s — reply: {bytes(rx).hex().upper()}"
        return f"linked {took:.1f}s, silent to BA33"
    except OSError:
        return f"linked {took:.1f}s, socket died on probe"


for ch in (1, 2, 3, 8, 11, 17, 25):
    print(f"channel {ch:2d}: {probe(ch)}", flush=True)
