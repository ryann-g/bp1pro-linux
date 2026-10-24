"""GUI smoke test on the real X display (no clicks needed — handlers direct).

Launches the Gtk window with the mock transport, exercises Connect / ANC /
EQ / gesture / toggle handlers, pumps the main loop, and checks the InfoBar
and status text respond. Skips silently when no display is available.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("DISPLAY"):
    print("no DISPLAY — skipping GUI smoke test")
    sys.exit(0)

import gi  # type: ignore
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # type: ignore

from bp1pro import gui as guimod  # noqa: E402


def pump(seconds: float) -> None:
    """Run the Gtk main loop long enough for idle callbacks."""
    end = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        GLib.usleep(10_000)


class GuiSmoke(unittest.TestCase):
    def setUp(self):
        self.app = guimod.Gui(mock=True)
        self.app.show_all()
        pump(0.4)

    def tearDown(self):
        self.app.destroy()
        pump(0.2)

    def test_mock_connect_and_anc(self):
        app = self.app
        app.on_connect()
        pump(1.0)
        self.assertIsNotNone(app.tr)
        self.assertTrue(app.tr.connected)
        # the mock's fresh-link burst (battery + case) must fill the row
        batt = app.batt_lbl.get_text()
        self.assertIn("L 100%", batt)
        self.assertIn("case 100%", batt)
        # ANC handler writes 2 frames in ONE logged batch
        app.anc_combo.set_active_id("1")
        app.on_anc()
        pump(1.0)
        log_text = app.log_buffer.get_text(
            app.log_buffer.get_start_iter(),
            app.log_buffer.get_end_iter(), False)
        self.assertIn("BA34", log_text)
        self.assertIn("AA33", log_text)

    def test_toggle_without_link_shows_infobar(self):
        app = self.app
        app._close_link()
        revert = app._send_toggle(lambda on: "BA240311", True)
        pump(0.3)
        self.assertTrue(revert)  # switch must revert when no link
        self.assertTrue(app.infobar.get_revealed())

    def test_real_connect_path_runs_status_burst(self):
        """Regression: _do_connect once idle_add'ed _after_connect from the
        worker thread; it ran BEFORE _on_done cleared _busy, so every real
        (non-mock) connect's status/battery burst was dropped with
        "dialogue busy".  This drives the real _do_connect ordering with a
        canned FakeLink instead of the mock fast-path."""
        import unittest.mock

        class FakeLink(guimod.BaseTransport):
            def __init__(self, addr: str) -> None:
                super().__init__(addr, None)
                self.connected = False
                self.channel: int | None = None
                self._last = ""

            def connect(self) -> None:
                self.channel = 1
                self.connected = True

            def send_hex(self, frame: str) -> None:
                self._last = frame

            def _drain(self, timeout: float, expect: str | None) -> list[str]:
                if self._last.startswith("BA33"):
                    return ["AA330100", "AA0264006401", "AA276400"]
                if self._last.startswith("BA42"):
                    return ["AA4200"]
                return []

        app = self.app
        app.mock_switch.set_active(False)
        pump(0.2)
        if not app.addr_combo.get_active_text():
            app.addr_combo.append_text("00:11:22:33:44:55")
            app.addr_combo.set_active(
                app.addr_combo.get_model().iter_n_children(None) - 1)
        # pump INSIDE the patch context: on_connect runs _do_connect on a
        # worker thread, so the factory may be called after on_connect
        # returns
        with unittest.mock.patch.object(
                guimod.device, "ensure_connected", lambda addr: True), \
                unittest.mock.patch.object(
                    guimod, "RfcommTransport",
                    lambda addr, channel=None: FakeLink(addr)):
            app.on_connect()
            pump(1.5)
        self.assertIsNotNone(app.tr)
        self.assertTrue(app.tr.connected)
        # battery row must fill from the fresh-link burst
        batt = app.batt_lbl.get_text()
        self.assertIn("L 100%", batt,
                      "battery row must fill on a real-path connect")
        self.assertIn("case 100%", batt)
        log_text = app.log_buffer.get_text(
            app.log_buffer.get_start_iter(),
            app.log_buffer.get_end_iter(), False)
        self.assertNotIn("dialogue busy", log_text)
        self.assertIn("AA4200", log_text)  # full status burst ran, not dropped

    def test_pending_queue_after_connect(self):
        app = self.app
        app._close_link()
        app._send("EQ queued", "BA3101", expect="AA30")
        self.assertTrue(app.infobar.get_revealed())
        pump(1.4)  # allow auto-connect + flush
        self.assertIsNotNone(app.tr)
        log_text = app.log_buffer.get_text(
            app.log_buffer.get_start_iter(),
            app.log_buffer.get_end_iter(), False)
        self.assertIn("BA3101", log_text)


if __name__ == "__main__":
    unittest.main()
