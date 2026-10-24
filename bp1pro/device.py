"""bluetoothctl helpers: pair/connect/status for the BP1 Pro.

Only wraps the ``bluetoothctl`` CLI (ships with BlueZ on every distro) —
no python-dbus dependency. All library functions return data and log via the
``bp1pro.device`` logger; printing belongs to the CLI/TUI layer.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess

log = logging.getLogger(__name__)

PAIR_GUIDE = """\
Pairing once, using bluetoothctl (or your desktop's Bluetooth panel):

  1. Put the earbuds in pairing mode (open the charging case, press the
     button on the case until the LED blinks / "Baseus Bass BP1 Pro" pairs).
  2. Run:
       $ bluetoothctl
         power on
         scan on            -> wait for the earbuds to appear, note the MAC
         scan off
         pair <MAC>
         trust <MAC>        -> auto reconnect later
         connect <MAC>      -> registers the audio (A2DP) profile
  3. Leave the buds connected; then run:  bp1pro status <MAC>
"""


def _bluetoothctl() -> str:
    path = shutil.which("bluetoothctl")
    if path is None:
        raise FileNotFoundError("bluetoothctl not found (install BlueZ)")
    return path


def _run_ctl(*args: str, timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(
            [_bluetoothctl(), *args],
            capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        log.warning("bluetoothctl %s timed out", " ".join(args))
        return ""
    if proc.returncode != 0:
        log.debug("bluetoothctl rc=%d out=%s err=%s",
                  proc.returncode, proc.stdout, proc.stderr)
    return proc.stdout + proc.stderr


def info(addr: str) -> dict[str, str]:
    """Device properties from ``bluetoothctl info <addr>`` (lowercased keys)."""
    out = _run_ctl("info", addr)
    props: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip().lower()
        if ":" in line and not line.startswith("device "):
            key, _, value = line.partition(":")
            props[key.strip()] = value.strip()
    return props


def is_connected(addr: str) -> bool | None:
    """True/False when known, None when the device isn't paired to this host."""
    props = info(addr)
    if "connected" not in props:
        return None
    return props["connected"].startswith("yes")


def ensure_connected(addr: str, timeout: float = 20.0) -> bool:
    """Return True when the earbuds report Connected: yes (connect if needed)."""
    state = is_connected(addr)
    if state is True:
        return True
    if state is None:
        log.warning("%s not paired on this host - run 'bp1pro pair' first", addr)
        return False
    log.info("waking %s via bluetoothctl connect", addr)
    _run_ctl("connect", addr, timeout=timeout)
    return bool(is_connected(addr))


def paired_devices() -> list[tuple[str, str]]:
    """(MAC, name) of devices known to this host."""
    out = _run_ctl("devices")
    rows = []
    for line in out.splitlines():
        if line.startswith("Device "):
            _, addr, name = line.split(" ", 2)
            rows.append((addr, name.strip()))
    return rows


def find_baseus() -> list[tuple[str, str]]:
    return [(a, n) for a, n in paired_devices() if "baseus" in n.lower()]


def adapter_ok() -> bool:
    """Check that a Bluetooth controller is present and powered."""
    out = _run_ctl("show")
    return "Powered: yes" in out and "Controller" in out


def audio_bus_ok() -> bool:
    """Indicator for PipeWire/PulseAudio presence (for music playback)."""
    return (pathlib.Path("/usr/bin/pipewire").exists()
            or pathlib.Path("/usr/bin/pipewire-pulse").exists()
            or pathlib.Path("/usr/bin/pulseaudio").exists())
