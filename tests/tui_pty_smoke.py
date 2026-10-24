"""Drive the TUI through a pty: startup, one prompt interaction, quit."""
import fcntl
import errno
import os
import pty
import struct
import sys
import termios
import time


def run() -> int:
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ["TERM"] = "xterm-256color"
        os.execvp(sys.executable,
                  [sys.executable, "-m", "bp1pro", "tui", "--mock"])
        return 1  # unreachable
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    # non-blocking: pump() polls instead of waiting for a full read
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    output = []

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            try:
                output.append(os.read(fd, 65536))
            except BlockingIOError:
                pass
            except OSError:
                break
            time.sleep(0.05)

    pump(1.5)          # initial draw + status sweep
    os.write(fd, b"j")  # cursor down (ANC mode)
    pump(0.4)
    os.write(fd, b"\r")  # open the ANC prompt
    pump(0.6)
    os.write(fd, b"0\r")  # choose off
    pump(0.8)
    os.write(fd, b"j\r")  # next row = strength
    pump(0.5)
    os.write(fd, b"55\r")  # level 55
    pump(0.8)
    os.write(fd, b"q")    # quit
    pump(0.6)
    os.close(fd)
    pid_gone, status = os.waitpid(pid, 0)
    blob = b"".join(output).decode(errors="replace")
    print(f"child exit status: {status}")
    markers = ("bp1pro 00:11:22:33:44:55", "AA33", "ANC mode", "BA340037",
               "AA3401")
    hit = [m for m in markers if m in blob]
    print("saw:", hit)
    tail = [line.strip() for line in blob.splitlines()
            if line.strip()][-28:]
    print("--- TAIL ---")
    print("\n".join(tail))
    return 0 if "AA3401" in blob else 1


if __name__ == "__main__":
    sys.exit(run())
