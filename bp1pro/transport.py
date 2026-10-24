"""RFCOMM/SPP transport for classic-Bluetooth Baseus headphones.

The phone app opens an SPP (UUID 00001101) *client* socket to the earbuds and
writes plain hex frames (protocol.py). On Linux we do the same with a raw
``AF_BLUETOOTH`` / ``BTPROTO_RFCOMM`` socket. The SPP server channel on the
BP1 Pro is 1 (verified live on 2026-10-24: link + BA33 -> AA33 answered);
channel 2 is the fallback order. Discovery probes each channel with the
harmless ``BA33`` query; an ``AA33``-prefixed reply confirms the control
channel.

Operational facts baked into this module:

* The SPP control channel is single-session: a phone running the Baseus app
  can occupy it, and then every connect attempt is refused. The error text
  says exactly that so the user can free the channel.
* The kernel can leave an ``EBUSY`` pending state behind a timed-out
  connect; retrying after a short sleep clears it.
* The bluetooth adapter on some laptops gets rfkill-soft-blocked (acer
  wireless driver events). ``repair_adapter()`` best-effort recovers it
  before connecting — no root needed for ``rfkill unblock bluetooth``.
* earbuds sleep when placed into the case; links then fail with
  ``EHOSTUNREACH`` until they are worn/removed.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from typing import Callable, Optional

from . import protocol

log = logging.getLogger(__name__)

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"  # app: ClassicBluetoothManager
# Real SPP server channel is 1 (live-verified); 2 sometimes half-registers,
# 3/8 are hopeful extras. Keep the list short: long scans take seconds each.
DEFAULT_SCAN_CHANNELS = (1, 2, 3, 8)

EBUSY = 16
ECONNREFUSED = 111
UNREACHABLE_ERRNOS = {113, 110, 100, 101, 102, 103}

BUSY_RETRY = 3          # EBUSY: kernel pending-state clears within seconds
BUSY_PAUSE = 1.2        # seconds between EBUSY retries
REFUSED_PAUSE = 0.8     # seconds between a refused connect retry

PHONE_BUSY_HINT = (
    "Channel refused. The SPP control channel allows ONE client — a nearby "
    "phone running the Baseus app probably holds it. Turn off the phone's "
    "Bluetooth briefly, keep the earbuds worn / out of the case, then retry."
)


class TransportError(Exception):
    """Raised when the earbuds cannot be reached over SPP."""


# ---------------------------------------------------------------------------
# adapter repair ------------------------------------------------------------

def _rfkill_soft_blocked() -> bool:
    try:
        proc = subprocess.run(["rfkill", "list"], capture_output=True, text=True,
                              timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    blocked = False
    spoke_bt = False
    for line in (proc.stdout or "").splitlines():
        if "Bluetooth" in line:
            spoke_bt = True
        if spoke_bt and "Soft blocked: yes" in line:
            blocked = True
        elif spoke_bt and line.strip().startswith(":") and blocked is False:
            pass
        if spoke_bt and blocked:
            break
        if spoke_bt and line.startswith(":") and "Bluetooth" not in line:
            spoke_bt = False
    return blocked


def _adapter_powered() -> bool:
    try:
        proc = subprocess.run(["bluetoothctl", "show"], capture_output=True,
                              text=True, timeout=6, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True  # can't tell — assume fine rather than break working setups
    return "Powered: yes" in proc.stdout


def repair_adapter(quiet: bool = False) -> bool:
    """Best-effort recovery of a dead adapter (rfkill soft-block / power off).

    Runs without root: ``rfkill unblock bluetooth`` and
    ``bluetoothctl power on`` are permitted to normal users on stock BlueZ.

    Returns True when the adapter is (probably) usable afterwards.
    """
    ok = True
    if _rfkill_soft_blocked():
        try:
            subprocess.run(["rfkill", "unblock", "bluetooth"], timeout=5,
                           check=False)
            time.sleep(0.4)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("rfkill unblock failed: %s", exc)
            ok = False
    if ok and not _adapter_powered():
        try:
            subprocess.run(["bluetoothctl", "power", "on"], timeout=8,
                           check=False)
            time.sleep(0.6)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.warning("bluetoothctl power on failed: %s", exc)
            ok = False
    usable = ok and not _rfkill_soft_blocked() and _adapter_powered()
    if not usable:
        log.warning("adapter repair incomplete (rfkill/power)")
    return usable


class BaseTransport:
    """Interface shared by real and mock transports."""

    def __init__(self, addr: str, channel: int | None = None) -> None:
        self.addr = addr
        self.channel = channel
        self.connected = False
        self.sock: Optional[socket.socket] = None

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.connected = False

    # -- data --------------------------------------------------------------
    def send_hex(self, frame: str) -> None:
        if not self.sock:
            raise TransportError("not connected")
        data = bytes.fromhex(frame)
        log.debug("TX %s", frame)
        self.sock.send(data)

    def _recv_chunk(self, timeout: float) -> bytes:
        assert self.sock is not None
        self.sock.settimeout(max(timeout, 0.05))
        try:
            return self.sock.recv(256)
        except socket.timeout:
            return b""
        except ConnectionResetError:
            raise TransportError("connection reset by earbuds") from None

    def exchange(self, frame: str, timeout: float = 1.5,
                 expect: str | None = None) -> list[str]:
        """Send one frame, collect reply frames until timeout.

        ``expect``: optional reply prefix (e.g. "AA33"); keep reading until a
        frame with that prefix arrives (or timeout). All received frames are
        returned in order.
        """
        self.send_hex(frame)
        return self._drain(timeout, expect)

    def _drain(self, timeout: float, expect: str | None) -> list[str]:
        assert self.sock is not None
        pending, self.pending = self.pending, []
        rx = bytearray()
        frames: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self._recv_chunk(0.5 if not frames else 0.35)
            if not chunk:
                if frames:
                    break  # short listen window after first reply burst
                if pending:
                    break  # only leftover probe frames queued — return them
                continue
            rx.extend(chunk)
            got, consumed = protocol.extract_frames(bytes(rx))
            del rx[:consumed]
            frames.extend(got)
            if expect and any(f.startswith(expect) for f in frames):
                break
        if expect and any(f.startswith(expect) for f in frames):
            # trailing grace: the buds often append battery/case pushes a
            # beat after the expected reply; one short read pulls them in
            chunk = self._recv_chunk(0.25)
            if chunk:
                rx.extend(chunk)
                got, consumed = protocol.extract_frames(bytes(rx))
                del rx[:consumed]
                frames.extend(got)
        out = pending + frames
        for f in out:
            log.debug("RX %s (%s)", f, protocol.decode(f))
        return out

    def __enter__(self) -> "BaseTransport":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# real transport ------------------------------------------------------------

class RfcommTransport(BaseTransport):
    """Raw AF_BLUETOOTH RFCOMM client with automatic channel discovery."""

    def __init__(self, addr: str, channel: int | None = None,
                 timeout: float = 2.0,
                 sock_factory: Callable[[], socket.socket] | None = None) -> None:
        super().__init__(addr, channel)
        self.timeout = timeout
        self.pending: list[str] = []  # frames read before the first exchange
        self._sock_factory = sock_factory or self._new_socket

    @staticmethod
    def _new_socket() -> socket.socket:
        return socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                             socket.BTPROTO_RFCOMM)

    def connect(self) -> None:
        """Dial the earbuds, auto-repairing the adapter and retrying busy
        states. Prefers proven channels; when ``channel`` is None the short
        DEFAULT_SCAN_CHANNELS list is used."""
        self.pending = []  # reset per link (frames read before this exchange)
        if self.channel is not None:
            self._connect_channel(self.channel)
            return
        repair_adapter()
        last_reason = "no channel answered"
        for ch in DEFAULT_SCAN_CHANNELS:
            try:
                self._connect_channel(ch)
                return
            except ChannelUnreachableError as exc:
                last_reason = str(exc)
                if repair_adapter():
                    log.info("adapter repaired after unreachable; retry ch %d",
                             ch)
                    try:
                        self._connect_channel(ch)
                        return
                    except ChannelUnreachableError:
                        break
                break
            except ChannelClosedError as exc:
                last_reason = str(exc)
        raise TransportError(
            "no working SPP channel on " + self.addr
            + " (tried " + ",".join(str(c) for c in DEFAULT_SCAN_CHANNELS)
            + "). Ensure the earbuds are worn / out of the case and the audio "
            "link is up (bluetoothctl connect). " + last_reason)

    def _connect_channel(self, channel: int) -> None:
        """One channel: dial (with busy/retry budget), probe BA33, keep link."""
        last_errno: int | None = None
        for attempt in range(BUSY_RETRY):
            if attempt:
                time.sleep(BUSY_PAUSE if last_errno == EBUSY
                           else REFUSED_PAUSE)
            sock = self._sock_factory()
            try:
                sock.settimeout(self.timeout)
                sock.connect((self.addr, channel))
                break
            except OSError as exc:
                sock.close()
                last_errno = exc.errno
                if exc.errno in UNREACHABLE_ERRNOS:
                    raise ChannelUnreachableError(str(exc)) from exc
                if exc.errno in (EBUSY, ECONNREFUSED):
                    continue
                raise ChannelClosedError(str(exc)) from exc
        else:
            # retry budget exhausted without a working link
            if last_errno == ECONNREFUSED:
                raise ChannelClosedError(
                    f"channel {channel} refused connection. "
                    + PHONE_BUSY_HINT)
            raise ChannelClosedError(
                f"channel {channel} repeatedly busy (errno {last_errno})")
        try:
            sock.send(bytes.fromhex(protocol.anc_query()))
            probe, extras = self._await_probe(sock)
        except (OSError, TransportError):
            sock.close()
            raise ChannelClosedError(
                f"channel {channel} did not answer probe") from None
        if not _probe_ok(probe):
            # link accepted but nothing speaks the Baseus control protocol
            sock.close()
            raise ChannelClosedError(
                f"channel {channel} silent for the BA33 probe")
        self.sock = sock
        self.channel = channel
        self.connected = True
        self.pending = extras
        self.pending = extras  # battery/case pushes read with the probe reply
        log.info("SPP link on channel %d (%s)", channel, self.addr)

    def _await_probe(self, sock: socket.socket, timeout: float = 1.5
                     ) -> tuple[str, list[str]]:
        """Read until an AA33 frame shows up; '' when nothing answers.

        Frames arriving in the same burst (battery left/right AA02, case
        AA27, EQ ack …) come back as the second value — the probe would
        otherwise swallow them; the caller stashes them for the next
        exchange instead of losing them.
        """
        deadline = time.monotonic() + timeout
        rx = bytearray()
        while time.monotonic() < deadline:
            sock.settimeout(0.4)
            try:
                data = sock.recv(256)
            except socket.timeout:
                continue
            if not data:
                return "", []
            rx.extend(data)
            frames, consumed = protocol.extract_frames(bytes(rx))
            del rx[:consumed]
            for idx, frame in enumerate(frames):
                if frame.startswith("AA33"):
                    extras = [f for f in frames[idx + 1:]]
                    return frame, extras
        return "", []


class ChannelClosedError(TransportError):
    pass


class ChannelUnreachableError(TransportError):
    pass


def _probe_ok(frame: str) -> bool:
    return frame.startswith("AA33")


# ---------------------------------------------------------------------------
# mock transport ------------------------------------------------------------

class MockTransport(BaseTransport):
    """Fake SPP bus that echoes realistic firmware replies (no hardware)."""

    def __init__(self, addr: str = "00:11:22:33:44:55", channel: int = 1) -> None:
        super().__init__(addr, channel)
        self.sent: list[str] = []
        self.written: bytearray = bytearray()  # accepted SET frames
        self._queue: list[str] = []            # reply frames pending delivery
        self._burst_done = False               # fresh-link battery burst

    def connect(self) -> None:
        self.connected = True

    def _canned_for(self, frame: str) -> list[str]:
        head = frame[:4]
        self.sent.append(frame)
        if head == "BA34":
            self.written.extend(bytes.fromhex(frame))
            return ["AA3401"]
        if head == "BA31":
            return ["AA30" + frame[4:6]]
        if head.startswith("BA22"):
            self.written.extend(bytes.fromhex(frame))
            return ["AA2200" + frame[4:10]]
        if head == "BA26":
            return ["AA26" + frame[4:6]]
        if head == "BA24":
            return ["AA24" + frame[4:6]]
        if head == "BA33":
            out = ["AA330100"]  # ANC answering, level 0
            if not self._burst_done:
                # fresh SPP link: buds push battery + case with the burst
                self._burst_done = True
                out += ["AA0264006401", "AA276400"]
            return out
        if head == "BA42":
            return ["AA4201"]    # 8D state: music
        if head == "BA43":
            self.written.extend(bytes.fromhex(frame))
            return ["AA4301"]    # 8D set applied
        return []

    def _drain(self, timeout: float, expect: str | None) -> list[str]:
        out, self._queue = self._queue, []
        return out

    def exchange(self, frame: str, timeout: float = 1.5,
                 expect: str | None = None) -> list[str]:
        log.debug("TX(mock) %s", frame)
        self._queue = self._canned_for(frame)
        return self._drain(timeout, expect)
