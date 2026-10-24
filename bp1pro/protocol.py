"""BP1 Pro control-protocol frames (classic BT SPP, reply prefix ``AA``).

Reverse engineered from the Baseus Home app ("com.baseus.intelligent", v2.17.0.1).
All frames are uppercase hex strings without spaces, e.g. ``"BA3401"``.

Evidence (jadx dump, file:line):
- ``GestureBleManager.java:53-148``     BA21/BA22/BA23-BA26 builders, op codes
- ``NoiseReduceDataModel.java:909-930`` BA33 query / BA34 set
- ``NoiseReducePopWindow.java:905-980`` AA34 ack codes + AA33 layout (mode, level)
- ``Gu.estDataResolveManager.java:277-438`` gesture function-id -> label table
- ``GestureFunctionChooseViewModel.u()`` function byte = ``%02X`` of function id
- ``EarEqDefaultRegulationPresenter.java:346-369`` BA31 EQ select, AA30 reply
- ``PanoramicSoundViewModel.java:104/:132`` BA42 8D query / BA43 <mode> set
- ``PanoramicSoundViewModel.x():229`` AA42 state parse; ``SpatialSoundManger.java:96-111`` mode table
- ``EarphoneFunctionShowFragmentNewUI.java:628-711`` AA43 ack/failure codes
- ``EarphoneFunctionShowFragmentNewUI.java:1462`` BA24 01/00 audio mode
- ``HomeBleDataResolvePresenter.c0():1045`` AA27 case battery (push only)
- Transport: RFCOMM/SPP UUID 00001101 (ClassicBluetoothManager, DeviceManager.H0)

Frames of this model family carry NO checksum (that is the older Q0/789C
family, which excludes "Baseus Bass BP1 Pro").
"""

from __future__ import annotations

import json
import logging
import pathlib

log = logging.getLogger(__name__)

# Sentinel that means "leave this parameter unchanged on the firmwar" (wire: FF).
UNSET = 255

# --- ANC state -----------------------------------------------------------
# NoiseReduceDataModel / GestureDataResolveManager.j(): noise-mode titles
ANC_MODES = {
    0: "normal (noise off)",
    1: "noise cancelling",
    2: "transparency",
}

# The AA33/BA34 level byte carries two value spaces:
#   0..100  -> manual ANC strength, echoed back verbatim by the firmware
#              (live-verified 2026-10-24, tests/probe_anc_sweep.py: 0/25/50/99/100)
#   101..103-> named ANC presets, NOT strengths (NoiseReduceViewModel.r():
#              resource names noise_commute / noise_indoor / noise_outdoor, plus
#              str_custorm as the custom entry whose stored strength is <100)
#   255 (FF)-> unset/auto, the app shows the first preset (NoiseReducePopWindow
#              .T()/pair handler, lines 980-1001)
MAX_STRENGTH = 100
ANC_PRESETS = frozenset((101, 102, 103))

# --- gesture click -> wire op (GestureBleManager.f switch cases) ----------
CLICK_OPS = {
    "single": "01",   # cases 4/5/8/101/102/103
    "double": "03",   # cases 0/1/6
    "triple": "00",   # cases 2/3/7
    "long": "04",     # cases 9/10/11
}

# Gesture function ids -> user label (GestureDataResolveManager.g()).
FUNCTIONS = {
    0: "none",
    1: "play / pause",
    2: "previous track",
    3: "next track",
    4: "voice assistant",
    5: "low latency (game)",
    6: "ANC mode toggle",
    7: "panoramic (spatial) sound",
    8: "panoramic sound (game only)",
    9: "rapid mode",
    10: "light effect toggle",
    11: "volume up",
    12: "volume down",
    13: "bass boost",
    14: "sleep mode",
    16: "dynamic sound",
    18: "take photo",
    27: "switch connection mode",
    28: "microphone on/off",
}

# --- 8D ("panoramic" / spatial) sound --------------------------------------
# NOT an EQ preset: a separate DSP chain. PanoramicSoundViewModel.E() queries
# with BA42 (reply AA42 <mode>), H() sets with BA43 <mode> (reply AA43 ...).
# Mode ids == wire bytes: SpatialSoundManger.e() builds the tab list with
# soundTypeInt matched against PanoramicSoundViewModel.x()'s AA42 parse, and
# entries 00/01/03/04 hard-code the byte equal to the int.
# Device gating for "Baseus Bass BP1 Pro": DeviceManager h0/c1/d1/g0() all
# false and PanoramicSoundViewModel.A() false -> Common 0, Music 1, Cinema 2
# only (game 3 Bowie-only; fixed 4 / tracking 5 Bowie 30/35 Max + H1s Pro).
SPATIAL_MODES = {
    0: "off (common)",
    1: "music",
    2: "cinema",
    3: "game (other models)",
    4: "fixed (other models)",
    5: "tracking (other models)",
}

# AA43 ack/failure codes (EarphoneFunctionShowFragmentNewUI.java:628-711,
# EarEqDefaultRegulationActivityNewUi.java:990-1017).
SPATIAL_FAIL = {
    "0A": "rejected: LHDC codec active — 8D needs SBC/AAC",
    "0B": "rejected: LDAC codec active — 8D needs SBC/AAC",
    "0C": "rejected: connect both earbuds (double-connect) first",
    "0D": "rejected: cannot set during a call",
    "00": "off (buds answered current state)",
    "02": "cinema (buds answered current state)",
}

# Reply prefixes that have a known *total byte length*; used by the stream
# parser to decide whether a trailing partial frame should wait for more data.
FRAME_LENGTH = {"AA33": 4, "AA34": 3, "AA30": 3, "AA35": 3,
                "AA02": 6, "AA42": 3, "AA43": 3}

SEND_PREFIX = "BA"
REPLY_PREFIX = "AA"

_FAIL_TEXT = {
    "01": "ok",
    "0C": "rejected: activate Ambient ANC (double-connect) first",
    "0D": "rejected: cannot switch while in a call",
    "00": "rejected: wear the earbuds properly and retry",
}


def _hex(value: int, width: int = 2) -> str:
    """int -> width-digit uppercase hex ('%02X' equivalent)."""
    if value < 0 or value >= (1 << (4 * width)):
        raise ValueError(f"value {value} does not fit in {width} hex digits")
    return f"{value:0{width}X}"


def is_frame(text: str) -> bool:
    return isinstance(text, str) and len(text) >= 3 and len(text) % 2 == 0 \
        and all(ch in "0123456789ABCDEF" for ch in text)


# ---------------------------------------------------------------------------
# send builders -------------------------------------------------------------

def anc_query() -> str:
    """Query current ANC state; reply AA33 <mode> <level>. (NoiseReduceDataModel:909)"""
    return "BA33"


def anc_set(mode: int, level: int | None = None) -> str:
    """Set ANC mode (+ optional 0-100 strength). level=None/255 sends FF.

    Reply: AA34 01 = ok, AA34 0C/0D/00 = failure reason. (NoiseReduceDataModel:930)
    The official app only drives 0-99 for manual strength because it reserves
    byte values >=101 for preset ids; the firmware itself accepts and echoes
    the full 0-100 range (probe_anc_sweep.py, 2026-10-24).
    """
    return "BA34" + _hex(mode) + ("FF" if level in (None, UNSET) else _hex(level))


def eq_select(preset_id: int) -> str:
    """Select EQ preset (or bands when device supports custom param later).

    Reply: AA30 <id>. Failures arrive in the same byte:
    0C = connect both buds, 0D = cannot set during a call, 00 = generic fail
    (EarEqDefaultRegulationActivityNewUi.java:880-905).
    (EarEqDefaultRegulationPresenter.java:346/369)
    """
    return "BA31" + _hex(preset_id)


# --- custom EQ (8-band upload) ---------------------------------------------
# EarEqSelfDefinePresenter.G():155 builds "BA31 %02X(id) <suffix> <bands>"
# where <suffix> is "" for non-"Baseus Storm 1" models and the band block
# comes from A() + B() — 8 bands x 8 bytes:
#   freq   u16 little-endian        (Hz; DefineSelf.f34059a default 100..
#                                   10k, f34062d = 65..12k on some models)
#   gain   u16 LE int(gain*10+120)  (−12..+12 dB -> 0x00..0xF0; 0 dB = 0x78)
#   q      u16 LE int(q*10)         (q 1.0 -> 10)
#   filter %02X + "00"               (1.0 -> "0100")
def eq_custom(gains: list[float], freqs: list[int] | None = None,
              qs: list[float] | None = None, filt: int = 1,
              slot_id: int = 8) -> str:
    """Upload an 8-band custom EQ into slot ``slot_id`` (app uses dictSort).

    ``gains`` in dB (−12..+12). Defaults: the app's default frequency plan
    100/200/400/800/1000/3000/6000/10000 Hz, q 1.0, filter 1.
    Reply: AA30 <slot> (select-ack; failures 00/0C/0D as for ``eq_select``).
    """
    default_freqs = [100, 200, 400, 800, 1000, 3000, 6000, 10000]
    freqs = list(freqs) if freqs is not None else default_freqs
    qs = list(qs) if qs is not None else [1.0] * len(gains)
    if not (len(gains) == len(freqs) == len(qs) == 8):
        raise ValueError("eq_custom needs exactly 8 freq/gain/q values")
    sb = ["BA31", _hex(slot_id), ""]
    for freq, gain, q in zip(freqs, gains, qs):
        g = int(gain * 10 + 120)
        if not 0 <= g <= 240:
            raise ValueError("gain out of range −12..+12 dB: " + str(gain))
        sb.append(int(freq).to_bytes(2, "little").hex().upper())
        sb.append(g.to_bytes(2, "little").hex().upper())
        sb.append((int(q * 10) & 0xFFFF).to_bytes(2, "little").hex().upper())
        sb.append(f"{int(filt):02X}00")
    return "".join(sb)


def spatial_query() -> str:
    """Query 8D/spatial sound state; reply AA42 <mode>.

    (PanoramicSoundViewModel.E(): sends "BA42", line 104.)
    """
    return "BA42"


def spatial_set(mode: int) -> str:
    """Set 8D/spatial sound mode; reply AA43 <code>.

    (PanoramicSoundViewModel.H(): "BA43" + model, lines 126-134. Modes in
    SPATIAL_MODES; for BP1 Pro use 0/1/2 = off/music/cinema.)
    """
    return "BA43" + _hex(mode)


# ---------------------------------------------------------------------------
# EQ preset names ------------------------------------------------------------
# The official app's preset labels are cloud-side config (EqRegulationBean /
# EqSoundModeBean{dictSort,label}); nothing is baked into the APK (verified:
# no catalog JSON in assets) and the firmware only knows numeric ids. So the
# Linux tools keep a user-editable name map in the shared config file, keyed
# by preset id. Unlabeled ids stay "preset N" until you name them.
EQ_NAMES_KEY = "eq_names"
CONFIG_PATH = pathlib.Path.home() / ".config" / "bp1pro.json"


def load_eq_names(path: pathlib.Path | None = None) -> dict[int, str]:
    """Preset-id -> label map from the shared config ({} when none saved)."""
    try:
        data = json.loads((path or CONFIG_PATH).read_text())
    except (OSError, ValueError):
        return {}
    raw = data.get(EQ_NAMES_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    names: dict[int, str] = {}
    for key, label in raw.items():
        try:
            names[int(key, 0)] = str(label)
        except (ValueError, TypeError):
            continue
    return names


def save_eq_name(preset_id: int, label: str, path: pathlib.Path | None = None) -> None:
    """Persist one EQ preset name (merges into the shared config file)."""
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    names = data.get(EQ_NAMES_KEY)
    names = dict(names) if isinstance(names, dict) else {}
    names[str(preset_id)] = label
    data[EQ_NAMES_KEY] = names
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


def eq_label(preset_id: int, names: dict[int, str] | None = None) -> str:
    """Preset id -> label; falls back to 'preset N' when not named."""
    resolved = load_eq_names() if names is None else names
    return resolved.get(preset_id) or f"preset {preset_id}"


def gesture_query(button: int) -> str:
    """Query gesture config of one button; reply frame starts AA21-ish.
    (GestureBleManager.d)"""
    return "BA21" + _hex(button)


def gesture_set(click: str, function: int, side: str) -> str:
    """Bind a function to one click type on one/both earbud.

    click: single|double|triple|long  -> BA22 slot-1 op byte (CLICK_OPS)
    side:  left -> f=fR=FF, right -> fL=FF f=f, both -> f f.   FF = unchanged.
    """
    if click not in CLICK_OPS:
        raise ValueError(f"click must be one of {sorted(CLICK_OPS)}, got {click!r}")
    _hex(function)  # validation only (0-255)
    if side == "left":
        f_l, f_r = _hex(function), "FF"
    elif side == "right":
        f_l, f_r = "FF", _hex(function)
    elif side == "both":
        f_l = f_r = _hex(function)
    else:
        raise ValueError(f"side must be left|right|both, got {side!r}")
    return "BA22" + CLICK_OPS[click] + f_l + f_r


def inear_query() -> str:
    """Query in-ear detection state. (GestureBleManager.b)"""
    return "BA25"


def inear_set(on: bool) -> str:
    """Set in-ear detection on/off. (GestureBleManager.a)"""
    return "BA26" + (_hex(1) if on else _hex(0))


def mode_query() -> str:
    """Query audio/low-latency mode. (GestureBleManager.e)"""
    return "BA23"


def mode_set(on: bool) -> str:
    """Set audio/low-latency mode: BA24 01/00. (EarphoneFunctionShowFragmentNewUI.j)"""
    return "BA24" + (_hex(1) if on else _hex(0))


# ---------------------------------------------------------------------------
# receive parsing -----------------------------------------------------------

def extract_frames(buf: bytes) -> tuple[list[str], int]:
    """Split a raw RX byte buffer into hex frames.

    Mirrors the app: frames start at a 0xAA byte and run until the next 0xAA
    marker (app splits the accumulated hex string on "AA"). Leading non-AA
    bytes are sync garbage and get dropped. Returns (frames, consumed) where
    consumed tells the caller how many head bytes are safe to drop; a partial
    trailing frame of a known type is NOT consumed (waits for more bytes).
    """
    frames: list[str] = []
    pos = 0
    size = len(buf)
    while pos < size:
        if buf[pos] != 0xAA:
            pos += 1
            continue
        nxt = buf.find(b"\xAA", pos + 1, size)
        chunk = buf[pos: size if nxt == -1 else nxt]
        needed = _expected_frame_len(chunk)
        if nxt == -1 and needed is not None and len(chunk) < needed:
            break  # partial known frame at end of buffer -> wait for the rest
        frames.append(chunk.hex().upper())
        pos = size if nxt == -1 else nxt
    return frames, pos


def _expected_frame_len(chunk: bytes) -> int | None:
    """Total length of a partial frame still arriving for a known reply type.

    Returns None when the frame cannot be recognized (emit as-is) or when it
    is already long enough.
    """
    if len(chunk) < 2:
        return 3  # bare 0xAA: wait until the type byte arrives (min frame)
    prefix = chunk[:2].hex().upper()
    expected = FRAME_LENGTH.get(prefix)
    if expected is None or len(chunk) >= expected:
        return None
    return expected


def decode(frame: str) -> str:
    """Human-readable description of a device->host frame."""
    if not is_frame(frame) or not frame.startswith(REPLY_PREFIX):
        return "(not a device frame)"
    head = frame[:4]
    payload = frame[4:]
    if head == "AA33":
        return _decode_anc(payload)
    if head == "AA34":
        return f"ANC ack: {_FAIL_TEXT.get(payload[:2], 'status 0x' + payload)}"
    if head == "AA30":
        return _decode_eq_ack(payload)
    if head == "AA02":
        return _decode_battery(payload)
    if head == "AA23":
        return f"sound settings reply: 0x{payload}"
    if head == "AA27":
        return _decode_case_battery(payload)
    if head == "AA42":
        return _decode_spatial_state(payload)
    if head == "AA43":
        return _decode_spatial_ack(payload)
    if frame == "AA3501":
        return "wear detected (earbuds put on)"
    if frame == "AA3500":
        return "taken off (or wear lost)"
    if head == "AA24":
        return f"audio-mode ack: 0x{payload}"
    return "device frame " + frame


def _decode_eq_ack(payload: str) -> str:
    """AA30 <id> — preset select ack; the byte may instead be a failure code
    (EarEqDefaultRegulationActivityNewUi.java:880-905: 00 generic fail (only
    sent on '00' branch), 0C double-connect required, 0D in a call)."""
    try:
        pid = int(payload[:2], 16)
    except (ValueError, IndexError):
        return "EQ ack frame " + payload
    if pid == 0x00:
        return "EQ select failed (device unresponsive — retry)"
    if pid == 0x0C:
        return "EQ select rejected: connect both buds first"
    if pid == 0x0D:
        return "EQ select rejected: cannot set during a call"
    return f"EQ ack: preset {pid} selected ({eq_label(pid)})"


def _decode_spatial_state(payload: str) -> str:
    """AA42 <mode> — current 8D/spatial state (PanoramicSoundViewModel.x:229)."""
    try:
        mode = int(payload[:2], 16)
    except (ValueError, IndexError):
        return "8D state frame " + payload
    if mode == 0:
        return "8D spatial: off"
    return (f"8D spatial: {SPATIAL_MODES.get(mode, 'unknown')} "
            f"(state 0x{payload[:2]})")


def _decode_spatial_ack(payload: str) -> str:
    """AA43 <code> — set ack: 01 applied; 0A/0B/0C/0D failure reasons
    (EarphoneFunctionShowFragmentNewUI.java:628-711); other bytes mirror the
    buds' 8D state."""
    code = payload[:2]
    if code == "01":
        return "8D spatial: applied"
    if code in SPATIAL_FAIL:
        return f"8D spatial: {SPATIAL_FAIL[code]}"
    try:
        mode = int(code, 16)
    except ValueError:
        return "8D ack frame " + payload
    return f"8D spatial: {SPATIAL_MODES.get(mode, 'state ' + code)}"


def _decode_battery(payload: str) -> str:
    """AA02 <L> 00 <R> 01 — earbud battery; regex proven by BleUtils.d()."""
    parts = [payload[i:i + 2] for i in range(0, min(len(payload), 8), 2)]
    try:
        left = int(parts[0], 16) if parts else None
        right = int(parts[2], 16) if len(parts) > 2 else None
    except ValueError:
        return "battery frame " + payload
    text = "battery:"
    if left is not None:
        text += f" left {left}"
    if right is not None:
        text += f", right {right}"
    return text


def _decode_case_battery(payload: str) -> str:
    """AA27 <q> — electricQuantity byte (EarphoneFunctionShowFragmentNewUI
    .t(): ``Integer.parseInt(electricQty.substring(4,6), 16)``)."""
    try:
        level = int(payload[:2], 16)
    except (ValueError, IndexError):
        return "case battery frame " + payload
    return f"charging-case battery {level}"


def _decode_anc(payload: str) -> str:
    if len(payload) < 4:
        return "ANC status: " + payload
    mode = int(payload[0:2], 16)
    level = int(payload[2:4], 16)
    name = ANC_MODES.get(mode, f"mode {mode}")
    if level == UNSET:
        strength = ", strength auto (unset)"
    elif level <= MAX_STRENGTH:
        strength = f", strength {level}"
    elif level in ANC_PRESETS:
        strength = f", preset {level - 100}"
    else:
        strength = f", preset id {level}"
    return f"ANC status: {name}{strength}"
