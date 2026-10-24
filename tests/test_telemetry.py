"""Unit tests for bp1pro.telemetry (battery fold, records, low-battery alerts)."""
import unittest

from bp1pro import telemetry


class FoldFrames(unittest.TestCase):
    """AA02 / AA27 battery frames folded into one state dict."""

    def test_battery_frame_sets_left_right(self):
        state = telemetry.fold_frames({}, ["AA0264006401"])
        self.assertEqual(state, {"left": 100, "right": 100, "case": None})

    def test_case_frame_sets_case(self):
        state = telemetry.fold_frames({}, ["AA2732"])
        self.assertEqual(state, {"left": None, "right": None, "case": 50})

    def test_frames_accumulate(self):
        state = telemetry.fold_frames({}, ["AA02640A3201"])
        state = telemetry.fold_frames(state, ["AA2755"])
        self.assertEqual(state, {"left": 100, "right": 50, "case": 85})

    def test_other_frames_ignored(self):
        state = telemetry.fold_frames(
            {"left": 7, "right": 8, "case": 9}, ["AA330165", "AA3002"])
        self.assertEqual(state, {"left": 7, "right": 8, "case": 9})

    def test_bad_hex_does_not_raise(self):
        state = telemetry.fold_frames({}, ["AA02ZZ0000", "AA27ZZ"])
        self.assertEqual(state, {"left": None, "right": None, "case": None})


class Records(unittest.TestCase):
    """Normalized records + CSV serialization."""

    def test_record_holds_state_and_time(self):
        rec = telemetry.battery_record({"left": 80, "right": 70, "case": 100})
        self.assertEqual((rec["left"], rec["right"], rec["case"]),
                         (80, 70, 100))
        self.assertRegex(rec["ts"], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_csv_header(self):
        self.assertEqual(telemetry.csv_header(), "ts,left,right,case")

    def test_csv_row_with_missing_values(self):
        row = telemetry.csv_row(telemetry.battery_record({"left": 25}))
        self.assertRegex(row, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2},25,,$")


class LowBatteryAlerts(unittest.TestCase):
    """One alert per drop; hysteresis re-arm (threshold + REARM_DELTA)."""

    def test_no_alert_above_threshold(self):
        alerts = telemetry.LowBatteryAlerts().update(
            {"left": 90, "right": 40, "case": 100})
        self.assertEqual(alerts, [])

    def test_crossing_fires_once(self):
        alerts = telemetry.LowBatteryAlerts()
        self.assertEqual(alerts.update({"left": 24}), [("left", 24)])
        self.assertEqual(alerts.update({"left": 23}), [])  # stays disarmed
        self.assertEqual(alerts.update({"left": 40}), [])  # re-arms (no alert)

    def test_hysteresis_rearm(self):
        alerts = telemetry.LowBatteryAlerts(threshold=25)
        self.assertEqual(alerts.update({"left": 25}), [("left", 25)])
        self.assertEqual(alerts.update({"left": 40}), [])       # re-arm
        self.assertEqual(alerts.update({"left": 34}), [])       # still armed
        self.assertEqual(alerts.update({"left": 24}),
                         [("left", 24)])                        # fires again

    def test_kinds_are_independent(self):
        alerts = telemetry.LowBatteryAlerts()
        self.assertEqual(alerts.update({"left": 20, "case": 26}),
                         [("left", 20)])
        self.assertEqual(alerts.update({"case": 10}), [("case", 10)])

    def test_missing_values_skipped(self):
        alerts = telemetry.LowBatteryAlerts()
        self.assertEqual(alerts.update({"left": None, "right": None}), [])

    def test_rearm_resets_all(self):
        alerts = telemetry.LowBatteryAlerts()
        self.assertEqual(alerts.update({"left": 5, "right": 4, "case": 3}),
                         [("left", 5), ("right", 4), ("case", 3)])
        alerts.rearm()
        self.assertEqual(alerts.update({"left": 25}), [("left", 25)])


if __name__ == "__main__":
    unittest.main()
