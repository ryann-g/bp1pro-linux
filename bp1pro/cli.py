"""Command-line front end: python -m bp1pro <cmd> [args] [-a MAC] [--mock|].

Subcommands: status, pair, connect, anc, eq, eq-custom, eq-name, inear, mode,
gesture, gesture-q, send, monitor, tui.

MAC address: give it after the subcommand, via -a/--address, or set
$BP1PRO_ADDR. Mock mode (--mock) drives a fake earbud for demos/tests.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Callable, Optional

from . import device, protocol
from . import telemetry
from .transport import BaseTransport, MockTransport, RfcommTransport, TransportError

log = logging.getLogger(__name__)

ANC_MODE_CODES = {"off": 0, "nc": 1, "transparency": 2}  # CLI symbol -> wire
SPATIAL_CODES = {name: code for code, name in
                 enumerate(("off", "music", "cinema", "game",
                            "fixed", "tracking"))}


def _transport(args: argparse.Namespace) -> BaseTransport:
    cls: type[BaseTransport] = RfcommTransport
    if args.mock:
        cls = MockTransport
    else:
        if not device.ensure_connected(args.addr):
            raise TransportError(
                f"{args.addr} is not connected (pair + bluetoothctl connect "
                "first; earbuds must be out of the case)")
    return cls(args.addr, channel=args.channel)


def cmd_status(args: argparse.Namespace) -> int:
    if args.mock:
        print(f"[mock] transport -> {args.addr}")
    else:
        if not device.adapter_ok():
            print("bluetoothctl reports no powered controller")
            return 1
        props = device.info(args.addr)
        wanted = {"name": "name", "connected": "connected", "paired": "paired",
                  "battery percentage": "battery"}
        shown = [f"{k}={props[v]}" for v, k in wanted.items() if v in props]
        print(args.addr, " ".join(shown) if shown else "(unknown)")
    with _transport(args) as tr:
        for frame in (protocol.anc_query(), protocol.inear_query(),
                      protocol.mode_query(), protocol.spatial_query()):
            for reply in tr.exchange(frame):
                print("  RX", reply, "-", protocol.decode(reply))
    return 0


def cmd_pair(_args: argparse.Namespace) -> int:
    print(device.PAIR_GUIDE)
    others = device.find_baseus()
    if others:
        print("Baseus devices already paired on this host:")
        for addr, name in others:
            print(f"  {addr}  {name}")
    else:
        print("No Baseus devices paired on this host yet.")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    ok = device.ensure_connected(args.addr)
    print(args.addr, "connected" if ok else "NOT connected")
    return 0 if ok else 1


def _announce(frames: list[str]) -> None:
    for frame in frames:
        print("  RX", frame, "-", protocol.decode(frame))


def cmd_anc(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        if args.mode is None:
            _announce(tr.exchange(protocol.anc_query()))
            return 0
        frame = protocol.anc_set(ANC_MODE_CODES[args.mode], args.level)
        print("TX", frame)
        _announce(tr.exchange(frame, expect="AA34"))
        return 0


def cmd_eq(args: argparse.Namespace) -> int:
    if args.preset is None:
        names = protocol.load_eq_names()
        for pid in range(16):
            print(f"  {pid:02d}  {protocol.eq_label(pid, names)}")
        print("(preset names are local — bp1pro eq-name <id> <name> sets one)")
        return 0
    with _transport(args) as tr:
        print("TX", f"BA31{args.preset:02X}")
        _announce(tr.exchange(protocol.eq_select(args.preset),
                              expect="AA30"))
        return 0


def cmd_eq_name(args: argparse.Namespace) -> int:
    label = " ".join(args.name)
    protocol.save_eq_name(args.preset, label)
    print(f"EQ preset {args.preset} labelled '{label}'")
    return 0


def cmd_spatial(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        if args.mode is None:
            _announce(tr.exchange(protocol.spatial_query(), expect="AA42"))
            return 0
        frame = protocol.spatial_set(SPATIAL_CODES[args.mode])
        print("TX", frame)
        _announce(tr.exchange(frame, expect="AA43"))
        return 0


def cmd_inear(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        if args.setting is None:
            _announce(tr.exchange(protocol.inear_query()))
            return 0
        frame = protocol.inear_set(args.setting == "on")
        print("TX", frame)
        _announce(tr.exchange(frame))
        return 0


def cmd_mode(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        if args.setting is None:
            _announce(tr.exchange(protocol.mode_query()))
            return 0
        frame = protocol.mode_set(args.setting == "on")
        print("TX", frame)
        _announce(tr.exchange(frame))
        return 0


def cmd_gesture(args: argparse.Namespace) -> int:
    if args.function is None:
        # list mode: the firmware function ids bindable to clicks
        print("bindable gesture function ids (protocol.FUNCTIONS):")
        for fid, label in sorted(protocol.FUNCTIONS.items()):
            print(f"  {fid:5d}  {label}")
        print("usage: bp1pro gesture <click> <side> <id>"
              "   e.g. bp1pro gesture double both 16")
        return 0
    frame = protocol.gesture_set(args.click, args.function, args.side)
    with _transport(args) as tr:
        print("TX", frame)
        _announce(tr.exchange(frame))
        return 0


def cmd_gesture_query(args: argparse.Namespace) -> int:
    with _transport(args) as tr:
        print("TX", f"BA21{args.button:02X}")
        _announce(tr.exchange(protocol.gesture_query(args.button)))
        return 0


def cmd_eq_custom(args: argparse.Namespace) -> int:
    def _nums(text: str, cast=float) -> list:
        return [cast(x.strip()) for x in text.split(",") if x.strip()]

    try:
        frame = protocol.eq_custom(
            _nums(args.gain),
            freqs=_nums(args.freq, float) if args.freq else None,
            qs=_nums(args.q, float) if args.q else None,
            filt=args.filter, slot_id=args.slot)
    except ValueError as exc:
        print("invalid eq-custom arguments:", exc)
        return 1
    with _transport(args) as tr:
        print("TX", frame, f"({(len(frame) - 4) // 2} bytes after BA31)")
        _announce(tr.exchange(frame, expect="AA30"))
        return 0


def cmd_send(args: argparse.Namespace) -> int:
    frame = args.hex_.replace(" ", "").upper()
    if not protocol.is_frame(frame):
        print("not a plain hex frame:", args.hex_)
        return 1
    with _transport(args) as tr:
        print("TX", frame)
        _announce(tr.exchange(frame))
        return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    import json

    state: dict = {}
    csvfh = None
    if args.csv:
        csvfh = open(args.csv, "a", encoding="utf-8")
        csvfh.write(telemetry.csv_header() + "\n")
        csvfh.flush()
        print(f"appending battery rows to {args.csv}")
    try:
        with _transport(args) as tr:
            print("monitoring", args.addr, "- Ctrl+C to stop")
            while True:
                frames = tr.exchange(protocol.anc_query(), timeout=3.0)
                state = telemetry.fold_frames(state, frames)
                rec = telemetry.battery_record(state)
                if rec.get("left") is not None or rec.get("case") is not None:
                    if args.csv and csvfh is not None:
                        csvfh.write(telemetry.csv_row(rec) + "\n")
                        csvfh.flush()
                    if args.json:
                        print(json.dumps(rec), flush=True)
                    else:
                        for frame in frames:
                            print("  RX", frame, "-", protocol.decode(frame),
                                  flush=True)
    finally:
        if csvfh is not None:
            csvfh.close()


def cmd_tui(args: argparse.Namespace) -> int:
    from . import tui
    return tui.run(args)


# -- argparse wiring ---------------------------------------------------------

_HEX = lambda s: int(s, 0)  # noqa: E731 (allow e.g. 0x0b)


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS so parsing the subcommand (after the top-level) does not
    # clobber values given on the top-level command line.
    common.add_argument("-a", "--address", default=argparse.SUPPRESS,
                        metavar="MAC", help="earbud MAC (overrides $BP1PRO_ADDR)")
    common.add_argument("--channel", type=int, default=argparse.SUPPRESS,
                        metavar="N",
                        help="force SPP RFCOMM channel (default: scan 1..10)")
    common.add_argument("--mock", action="store_true", default=argparse.SUPPRESS,
                        help="drive a fake earbud (no hardware)")
    common.add_argument("-v", "--verbose", action="store_true",
                        default=argparse.SUPPRESS)
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="bp1pro",
        description="Control Baseus Bass BP1 Pro earbuds on Linux "
                    "(classic-BT SPP protocol, reverse engineered from the "
                    "Baseus app v2.17.0.1)",
        parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, helptext: str, func: Callable,
            parents: Optional[list] = None) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=helptext,
                           parents=[common, *(parents or [])])
        p.set_defaults(func=func)
        return p

    add("status", "query ANC/in-ear/audio-mode state", cmd_status)
    add("pair", "pairing walkthrough + known Baseus devices", cmd_pair)
    add("connect", "bluetoothctl connect the earbuds", cmd_connect)

    p = add("anc", "query/set noise control", cmd_anc)
    p.add_argument("mode", nargs="?", choices=sorted(ANC_MODE_CODES),
                   help="off | nc | transparency")
    p.add_argument("--level", type=int, default=None, help="0-100 (FF=leave)")

    p = add("eq", "query list / select EQ preset", cmd_eq)
    p.add_argument("preset", nargs="?", type=_HEX, default=None,
                   help="preset id (hex ok); omit to list saved names")

    p = add("eq-name", "name an EQ preset (stored in ~/.config/bp1pro.json)",
            cmd_eq_name)
    p.add_argument("preset", type=_HEX)
    p.add_argument("name", nargs="+")

    p = add("spatial", "query/set the 8D (spatial/panoramic) sound",
            cmd_spatial)
    p.add_argument("mode", nargs="?", choices=sorted(SPATIAL_CODES),
                   help="BP1 Pro: off | music | cinema")

    p = add("inear", "in-ear detect on/off", cmd_inear)
    p.add_argument("setting", nargs="?", choices=("on", "off"))

    p = add("mode", "low-latency audio mode on/off (BA24)", cmd_mode)
    p.add_argument("setting", nargs="?", choices=("on", "off"))

    p = add("gesture", "bind function to a click (no args: list functions)",
            cmd_gesture)
    p.add_argument("click", nargs="?", choices=sorted(protocol.CLICK_OPS))
    p.add_argument("side", nargs="?", choices=("left", "right", "both"),
                   default=None)
    p.add_argument("function", nargs="?", type=_HEX, default=None,
                   help="id, e.g. 1=play/pause 3=next 6=ANC 7=spatial"
                        " 13=bass 16=dynamic sound")

    p = add("eq-custom", "upload a custom 8-band EQ (BA31 + band payload)",
            cmd_eq_custom)
    p.add_argument("--gain", required=True,
                   help="8 dB values, comma list, −12..+12"
                        " e.g. 3,-3,0,0,0,0,2,5")
    p.add_argument("--freq", help="8 Hz values (default 100..10000 plan)")
    p.add_argument("--q", help="8 q factors (default 1.0)")
    p.add_argument("--slot", type=_HEX, default=8,
                   help="custom-EQ slot id (default 8)")
    p.add_argument("--filter", type=_HEX, default=1,
                   help="filter type byte (default 1)")
    p = add("gesture-q", "query a button's config (BA21)", cmd_gesture_query)
    p.add_argument("button", type=_HEX)

    p = add("send", "send raw hex frame", cmd_send)
    p.add_argument("hex_", metavar="HEX")

    p = add("monitor", "poll and print incoming frames (--json/--csv sinks)",
            cmd_monitor)
    p.add_argument("--json", action="store_true",
                   help="emit one JSON battery record per poll")
    p.add_argument("--csv", metavar="PATH",
                   help="append timestamped battery rows to a CSV file")
    add("tui", "full-screen terminal UI", cmd_tui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # fill attributes suppressed at top level
    for name, fallback in (("address", None), ("channel", None), ("mock", False),
                           ("verbose", False)):
        if not hasattr(args, name):
            setattr(args, name, fallback)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")
    if args.address:
        args.addr = args.address.upper()
    else:
        args.addr = os.environ.get("BP1PRO_ADDR", "").upper()
    if not args.mock and not args.addr and args.command not in ("pair",):
        parser.error("no earbud MAC: pass -a AA:BB:CC:DD:EE:FF or set "
                     "$BP1PRO_ADDR ('bp1pro pair' lists candidates)")
    try:
        return args.func(args)
    except TransportError as exc:
        print("error:", exc)
        return 1
    except KeyboardInterrupt:
        print("^C")
        return 0


if __name__ == "__main__":
    sys.exit(main())
