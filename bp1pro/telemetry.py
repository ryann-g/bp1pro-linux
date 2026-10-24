"""Battery/observation helpers shared by the CLI monitor and the GUI.

Pure stdlib; no transport knowledge — callers fold raw frames in and ask
questions: current battery state, alert crossings, one record per poll.
"""
from __future__ import annotations

import time

LOW_BATTERY_PCT = 25      # alert once a source drops to/below this
REARM_DELTA = 10          # hysteresis: re-arm after it climbs back up


def fold_frames(state: dict, frames: list[str]) -> dict:
    """Fold raw ``AA02``/``AA27`` battery frames into ``state`` (copy).

    ``state`` has keys "left" / "right" / "case" (percent ints or None);
    unknown entries in ``state`` are preserved, missing ones default to None.
    Frames look like ``AA0264006401`` (battery) and ``AA276400`` (case).
    """
    out = {"left": None, "right": None, "case": None, **state}
    for frame in frames:
        if frame.startswith("AA02") and len(frame) >= 8:
            try:
                out["left"] = int(frame[4:6], 16)
                out["right"] = int(frame[8:10], 16)
            except ValueError:
                pass
        elif frame.startswith("AA27") and len(frame) >= 6:
            try:
                out["case"] = int(frame[4:6], 16)
            except ValueError:
                pass
    return out


def battery_record(state: dict) -> dict:
    """One normalized record for CSV/JSON sinks (holds the sample time)."""
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "left": state.get("left"),
        "right": state.get("right"),
        "case": state.get("case"),
    }


def csv_header() -> str:
    return "ts,left,right,case"


def csv_row(rec: dict) -> str:
    return ",".join("" if rec.get(k) is None else str(rec.get(k))
                    for k in ("ts", "left", "right", "case"))


class LowBatteryAlerts:
    """Emit a ``(kind, percent)`` alert once per drop below the threshold.

    Hysteresis: a source only re-arms once it climbs at least ``REARM_DELTA``
    back above the threshold, so hovering battery levels don't spam.
    """

    def __init__(self, threshold: int = LOW_BATTERY_PCT) -> None:
        self.threshold = threshold
        self._armed = {"left": True, "right": True, "case": True}

    def update(self, batt: dict) -> list[tuple[str, int]]:
        alerts: list[tuple[str, int]] = []
        for kind in ("left", "right", "case"):
            value = batt.get(kind)
            if value is None:
                continue
            if not self._armed[kind]:
                if value >= self.threshold + REARM_DELTA:
                    self._armed[kind] = True
                continue
            if value <= self.threshold:
                self._armed[kind] = False
                alerts.append((kind, value))
        return alerts

    def rearm(self) -> None:
        self._armed = {"left": True, "right": True, "case": True}
