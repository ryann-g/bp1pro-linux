"""Zero-dependency terminal UI (curses) for the BP1 Pro console.

Layout:
    header line     -- device / transport state
    action list     -- cursor-navigated menu
    log pane        -- TX/RX history (raw bytes + decoded meaning)

Keys: up/down = move, Enter = run, q = quit. Sub-prompts ask for extra
values (ANC mode/level, EQ preset, gesture binding) on a bottom input line.
"""

from __future__ import annotations

import argparse
import curses
import logging
from typing import Callable, Optional

from . import protocol
from .transport import BaseTransport, MockTransport, RfcommTransport, TransportError

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

Runner = Callable[["Ui"], str]


class Action:
    def __init__(self, label: str, runner: Runner) -> None:
        self.label = label
        self.runner = runner


class Ui:
    """Screen + transport shared across menu actions."""

    def __init__(self, screen: "curses._CursesWindow",
                 transport: BaseTransport) -> None:
        self.scr = screen
        self.tr = transport
        self.actions: list[Action] = []
        self.log: list[str] = []
        self.cursor = 0
        self.header = "bp1pro"
        self.notice = "up/down: move - Enter: run - q: quit"

    # -- logging ---------------------------------------------------------
    def add_log(self, text: str) -> None:
        self.log.append(text)
        del self.log[:-300]
        self.draw()

    def send(self, frame: str, expect: str | None = None) -> list[str]:
        """Execute one exchange and record it."""
        self.add_log(f"TX {frame}")
        try:
            frames = self.tr.exchange(frame, expect=expect)
        except TransportError as exc:
            self.add_log(f"!! {exc}")
            return []
        for frame in frames:
            self.add_log(f"RX {frame}   ({protocol.decode(frame)})")
        return frames

    # -- input -----------------------------------------------------------
    def prompt(self, text: str, max_len: int = 14) -> str:
        """Read an answer from the bottom line; '' when shelved/empty."""
        maxy, _ = self.scr.getmaxyx()
        self.notice = text[:76] or "input:"
        self.draw()
        win = curses.newwin(1, 40, maxy - 1, 0)
        win.keypad(True)
        curses.echo()
        curses.curs_set(1)
        try:
            value = win.getstr(max_len).decode(errors="replace").strip()
        except curses.error:  # window resized meanwhile
            value = ""
        curses.curs_set(0)
        curses.noecho()
        self.notice = "up/down: move - Enter: run - q: quit"
        self.draw()
        return value

    # -- drawing -----------------------------------------------------------
    def draw(self) -> None:
        self.scr.erase()
        maxy, maxx = self.scr.getmaxyx()
        self.scr.addnstr(0, 0, self.header[:maxx - 1], maxx - 1, curses.A_REVERSE)
        self.scr.addnstr(1, 0, "-" * (maxx - 1), maxx - 1)
        for idx, action in enumerate(self.actions):
            attr = curses.A_REVERSE if idx == self.cursor else curses.A_NORMAL
            row = 2 + idx
            if row < maxy - 1:
                self.scr.addnstr(row, 0, f" {action.label}"[:maxx - 1],
                                 maxx - 1, attr)
        log_top = 2 + len(self.actions) + 1
        self.scr.addnstr(log_top, 0, "-" * (maxx - 1), maxx - 1)
        for row, text in enumerate(self.log[-(maxy - log_top - 2):],
                                   start=log_top + 1):
            if row < maxy - 1:
                self.scr.addnstr(row, 0, text[:maxx - 1], maxx - 1)
        self.scr.addnstr(maxy - 1, 0, self.notice[:maxx - 1], maxx - 1)
        self.scr.refresh()


# ---------------------------------------------------------------------------
# menu actions (receive the Ui instance) ------------------------------------

def _anc_title(mode_code: int) -> str:
    return {0: "normal (noise off)", 1: "noise cancelling",
            2: "transparency"}.get(mode_code, str(mode_code))


def a_status(ui: Ui) -> str:
    frames = ui.send(protocol.anc_query(), expect="AA33")
    ui.send(protocol.inear_query())
    ui.send(protocol.mode_query())
    ui.send(protocol.spatial_query())
    if frames:
        decoded = protocol.decode(frames[-1])
        ui.header = f"bp1pro {ui.tr.addr} ch{ui.tr.channel} | {decoded}"
        ui.draw()
    return ""


def a_spatial(ui: Ui) -> str:
    value = ui.prompt(
        "8D mode: 0=off 1=music 2=cinema (BP1 Pro; 3/4/5 only other models)")
    if not value:
        return ""
    ui.send(protocol.spatial_set(int(value, 0)), expect="AA43")
    ui.send(protocol.spatial_query())
    return ""


def a_eq(ui: Ui) -> str:
    value = ui.prompt("EQ preset id, or Enter to list saved names")
    if not value:
        names = protocol.load_eq_names()
        for pid in range(16):
            ui.add_log(f"  preset {pid:02d} = {protocol.eq_label(pid, names)}")
        return ""
    ui.send(protocol.eq_select(int(value, 0)), expect="AA30")
    return ""


def a_anc_mode(ui: Ui) -> str:
    value = ui.prompt("ANC mode: 0=off 1=noise cancelling 2=transparency")
    if not value:
        return ""
    mode = int(value, 0)
    frames = ui.send(protocol.anc_set(mode), expect="AA34")
    if frames and frames[-1].startswith("AA3401"):
        ui.header += f" | now: {_anc_title(mode)}"
    ui.draw()
    return ""


def a_anc_level(ui: Ui) -> str:
    value = ui.prompt("ANC strength 0-100 (0-FF accepted, Enter=keep)")
    if not value:
        return ""
    ui.send(protocol.anc_set(1, int(value, 0)), expect="AA34")
    return ""


def a_toggle(kind: str, builder: Callable[[bool], str]) -> Runner:
    def runner(ui: Ui) -> str:
        value = ui.prompt(f"{kind}: 1 = on, 0 = off")
        if not value:
            return ""
        ui.send(builder(value == "1"))
        return ""
    return runner


def a_gesture(ui: Ui) -> str:
    click = ui.prompt("click: single / double / triple / long")
    if click not in protocol.CLICK_OPS:
        return ""
    side = ui.prompt("side: l = left, r = right, b = both")
    sides = {"l": "left", "r": "right", "b": "both"}
    if side not in sides:
        return ""
    fun = ui.prompt("function id (Enter to list, h for names)")
    if fun in ("", "h"):
        for fid, name in sorted(protocol.FUNCTIONS.items()):
            ui.add_log(f"  function {fid:02d} = {name}")
        return ""
    ui.send(protocol.gesture_set(click, int(fun, 0), sides[side]))
    return ""


def a_gesture_query(ui: Ui) -> str:
    value = ui.prompt("button id (0=left bud, 1=right bud, ...)")
    if not value:
        return ""
    ui.send(protocol.gesture_query(int(value, 0)))
    return ""


def a_raw(ui: Ui) -> str:
    value = ui.prompt("raw hex frame, e.g. BA33")
    if not value:
        return ""
    frame = value.replace(" ", "").upper()
    if not protocol.is_frame(frame):
        ui.add_log(f"!! invalid hex frame {value!r}")
        return ""
    ui.send(frame)
    return ""


def a_reconnect(ui: Ui) -> str:
    ui.add_log("reconnecting...")
    try:
        ui.tr.close()
        ui.tr.connect()
    except TransportError as exc:
        ui.add_log(f"!! reconnect failed: {exc}")
        return ""
    ui.header = f"bp1pro {ui.tr.addr} ch{ui.tr.channel} - connected"
    ui.add_log("connected")
    return ""


def build_menu() -> list[Action]:
    return [
        Action("status - query ANC / in-ear / audio-mode state", a_status),
        Action("ANC: set mode (off / noise cancelling / transparency)", a_anc_mode),
        Action("ANC: adjust strength level", a_anc_level),
        Action("EQ: select preset (Enter lists names)", a_eq),
        Action("8D: set spatial mode (off/music/cinema)", a_spatial),
        Action("In-ear detect: on/off", a_toggle("in-ear detect", protocol.inear_set)),
        Action("Low-latency game mode: on/off", a_toggle("game mode", protocol.mode_set)),
        Action("Gesture: bind function to click", a_gesture),
        Action("Gesture: read button config", a_gesture_query),
        Action("Send raw hex frame", a_raw),
        Action("Reconnect SPP channel", a_reconnect),
    ]


# ---------------------------------------------------------------------------
# main loop -------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Entry used by ``bp1pro tui``; returns process exit code."""
    if args.mock:
        transport: BaseTransport = MockTransport(args.addr or "00:11:22:33:44:55")
    else:
        from . import device
        if not device.ensure_connected(args.addr):
            print(f"error: {args.addr} is not connected; pair and run "
                  "'bluetoothctl connect' first (earbuds out of the case)")
            return 1
        transport = RfcommTransport(args.addr, channel=args.channel)
    return curses.wrapper(_loop, transport)


def _loop(screen: "curses._CursesWindow", transport: BaseTransport) -> int:
    curses.curs_set(0)
    screen.keypad(True)
    ui = Ui(screen, transport)
    ui.actions = build_menu()
    try:
        transport.connect()
        ui.header = f"bp1pro {transport.addr} ch{transport.channel}"
        ui.add_log(f"connected on channel {transport.channel}")
        a_status(ui)
    except TransportError as exc:
        ui.header = f"bp1pro {transport.addr} - NOT connected"
        ui.add_log(f"!! {exc}")
        ui.actions = []
    while True:
        ui.draw()
        try:
            key = screen.getch()
        except KeyboardInterrupt:
            return 0
        if not ui.actions:
            if key == ord("q"):
                return 0
        elif key in (curses.KEY_UP, ord("k")):
            ui.cursor = (ui.cursor - 1) % len(ui.actions)
        elif key in (curses.KEY_DOWN, ord("j")):
            ui.cursor = (ui.cursor + 1) % len(ui.actions)
        elif key in (curses.KEY_ENTER, 10, 13):
            try:
                ui.actions[ui.cursor].runner(ui)
            except (ValueError, KeyError, TimeoutError, OverflowError) as exc:
                ui.add_log(f"!! {exc}")
        elif key == ord("q"):
            return 0
