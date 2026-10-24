#!/usr/bin/env python3
"""Live probe: does firmware accept/echo the full 0..100 ANC strength range?

Sends BA34 01 <level> for 0, 25, 50, 99 and 100, reads back AA33 after each,
then restores the initial (mode, level) pair. Every frame is printed with its
decoded meaning. Expect audible ANC changes during the sweep.

Result (2026-10-24): every byte 0..100 is accepted AND echoed back verbatim --
the manual strength space is 0..100. Values >=101 are preset ids (separate
value space), which is why the official app never sends 100 (its parsing
treats >=100 as the preset-id space; see NoiseReducePopWindow T(), line 983).
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/home/ryan/bp1pro-linux")

from bp1pro import device, protocol  # noqa: E402
from bp1pro.transport import RfcommTransport, TransportError  # noqa: E402

ADDR = "44:38:EB:xx:xx:xx"

# 0x00, 0x19 (25), 0x32 (50), 0x63 (99) -- full custom span;
# 0x64 (100) -- dead-zone probe, firmware may ignore it.
LEVELS = (0x00, 0x19, 0x32, 0x63, 0x64)


def dump(tag: str, frame: str, frames: list[str]) -> None:
    for f in frames:
        print(f"{tag:>12} {frame} -> {f}  # {protocol.decode(f)}")


def main() -> int:
    if not device.ensure_connected(ADDR):
        print(f"FAIL: BT link to {ADDR} not established "
              "(rfkill? buds in case? phone app holding SPP?)")
        return 2
    try:
        with RfcommTransport(ADDR) as tr:
            initial = [f for f in
                       tr.exchange(protocol.anc_query(), expect="AA33")
                       if f.startswith("AA33")]
            dump("initial", "BA33", initial)
            if not initial:
                print("FAIL: no AA33 reply to BA33 query")
                return 1
            mode = int(initial[0][4:6], 16)
            level = int(initial[0][6:8], 16)
            for lvl in LEVELS:
                set_frames = tr.exchange(protocol.anc_set(1, lvl),
                                         expect="AA34")
                dump(f"set {lvl:3d}", "BA34", set_frames)
                time.sleep(0.9)          # audible step for the listener
                echo = tr.exchange(protocol.anc_query(), expect="AA33")
                dump(f"echo {lvl:3d}", "BA33", echo)
            # Restore whatever the buds reported before the sweep.
            restore = protocol.anc_set(
                mode, None if level == protocol.UNSET else level)
            anc = tr.exchange(restore, expect="AA34")
            dump("restore", restore, anc)
            time.sleep(0.3)
            final = tr.exchange(protocol.anc_query(), expect="AA33")
            dump("final", "BA33", final)
    except TransportError as exc:
        print("FAIL:", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
