#!/usr/bin/env python3
"""Tray smoke: registers a real StatusNotifierItem on the session bus.

Needs a running GNOME shell with a StatusNotifier host (Zorin AppIndicators,
KDE, XFCE...). Verifies the D-Bus side: the item owns a bus name, the
watcher lists it, and an external client can read properties/layout and
drive a clicked() event (self-calls deadlock in dbus-python, so all client
traffic comes from a child process — like a real consumer).
"""
import os
import re
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # type: ignore  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # type: ignore  # noqa: E402

import dbus  # type: ignore  # noqa: E402

from bp1pro import tray as tray_mod  # noqa: E402

_CLIENT = r'''
import sys, dbus
name = sys.argv[1]
bus = dbus.SessionBus()
obj = bus.get_object(name, "/StatusNotifierItem", introspect=False)
props = dbus.Interface(obj, dbus.PROPERTIES_IFACE)
assert str(props.Get("org.kde.StatusNotifierItem", "Id")) == "bp1pro"
allp = props.GetAll("org.kde.StatusNotifierItem")
assert "IconName" in allp and "Menu" in allp, allp.keys()
menu_path = str(allp["Menu"])
mobj = bus.get_object(name, menu_path, introspect=False)
menu = dbus.Interface(mobj, "com.canonical.dbusmenu")
rev, layout = menu.GetLayout(0, -1, dbus.Array(["type", "children-display"],
                                               signature="s"))
nid, root_props, children = layout
assert int(nid) == 0
print("LAYOUT-CHILDREN", len(children))
print("MENU-PATH", menu_path)
menu.Event(8, "clicked", dbus.String(""), dbus.UInt32(0))
print("EVENT-DONE")
'''


def pump(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.02)


class TraySmoke(unittest.TestCase):
    def test_registration_menu_and_events(self):
        if not os.environ.get("DISPLAY"):
            self.skipTest("needs X11 + session bus")
        if os.popen("busctl --user list 2>/dev/null | grep -c "
                    "org.kde.StatusNotifierWatcher").read().strip() == "0":
            self.skipTest("no StatusNotifierWatcher on the session bus")
        Gtk.init()
        fired = []

        item = tray_mod.TrayIcon(actions={
            tray_mod.ID_QUIT: lambda: fired.append("quit"),
        })
        item.start()
        self.assertTrue(item.bus_name.startswith("org.kde.StatusNotifierItem"))
        pump(2.0)
        self.assertTrue(item.registered,
                        "watcher did not accept the item")

        # external watcher must list us
        watcher = item._bus.get_object(
            "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
            introspect=False)
        items = dbus.Interface(watcher, dbus.PROPERTIES_IFACE).Get(
            "org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems")
        self.assertIn(item.bus_name, items,
                      f"{item.bus_name} missing from {items}")

        # external client: property fetches, layout, then a click event
        item.render("L 90% · R 90% · C 100%", "noise cancelling", "off",
                    "tooltip test")
        pump(0.3)
        proc = subprocess.Popen([sys.executable, "-c", _CLIENT, item.bus_name],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        # keep pumping the main loop — the parent must dispatch the child's
        # calls while the child waits for replies
        deadline = time.monotonic() + 20
        while proc.poll() is None and time.monotonic() < deadline:
            pump(0.05)
        out, err = proc.communicate(timeout=5)
        self.assertIn("LAYOUT-CHILDREN 8", out,
                      f"layout wrong; child err: {err!r} out: {out!r}")
        self.assertIn("EVENT-DONE", out, f"event failed: {out}")
        menu_path = re.search(r"MENU-PATH (\S+)", out)
        self.assertTrue(menu_path and
                        menu_path.group(1) == item.state["menu_path"])
        pump(0.5)
        self.assertEqual(fired, ["quit"],
                         "clicked() did not reach the menu action")

        item.stop()
        pump(0.3)


if __name__ == "__main__":
    unittest.main()
