"""Probe SPP through bluetoothd's Profile API (bypasses raw-socket limits).

Registers org.bluez.Profile1 (UUID 00001101, role client) on the system bus,
asks bluetoothd to ConnectProfile on the earbuds, and receives the connected
RFCOMM fd via NewConnection. Then sends BA33 and prints the reply.

Run: python3 tests/probe_spp_profile.py [addr]
"""
import os
import select
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # type: ignore

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # type: ignore

UUID_SPP = "00001101-0000-1000-8000-00805f9b34fb"
if len(sys.argv) > 1:
    DEVICE_PATH = ("/org/bluez/hci0/dev_%s" %
                   sys.argv[1].replace(":", "_"))
else:
    DEVICE_PATH = "/org/bluez/hci0/dev_44_38_EB_56_E9_B7"

result = {"state": "init", "detail": "", "reply": ""}
loop = GLib.MainLoop()

PROFILE_XML = """<node>
  <interface name="org.bluez.Profile1">
    <method name="Release"/>
    <method name="NewConnection">
      <arg name="device" type="o" direction="in"/>
      <arg name="fd" type="h" direction="in"/>
      <arg name="opts" type="a{sv}" direction="in"/>
    </method>
    <method name="RequestDisconnection">
      <arg name="device" type="o" direction="in"/>
    </method>
    <method name="Cancel"/>
  </interface>
</node>"""


def on_call(_conn, _sender, _path, _iface, method, params, invocation):
    try:
        if method == "Release":
            invocation.return_value(None)
        elif method == "Cancel":
            result["state"] = "cancelled-by-bluetoothd"
            invocation.return_value(None)
            loop.quit()
        elif method == "RequestDisconnection":
            invocation.return_value(None)
        elif method == "NewConnection":
            device, fd_index, _props = params.unpack()
            try:
                fd_list = invocation.get_message().get_unix_fd_list()
                fd = fd_list.get(fd_index)
            except (GLib.Error, TypeError, ValueError, AttributeError) as exc:
                result["state"] = "fd-extract-failed"
                result["detail"] = repr(exc)
                invocation.return_value(None)
                loop.quit()
                return
            result["state"] = "fd-received"
            result["detail"] = f"fd={fd} device={device}"
            invocation.return_value(None)
            threading.Thread(target=handshake(fd), daemon=True).start()
    except (GLib.Error, OSError) as exc:
        result["state"] = "callback-error"
        result["detail"] = repr(exc)
        loop.quit()


def handshake(fd):
    def inner():
        try:
            os.write(fd, bytes.fromhex("BA33"))
            readable, _, _ = select.select([fd], [], [], 4.0)
            if readable == 0:
                result["state"] = "linked-but-silent-to-BA33"
                return
            data = os.read(fd, 256)
            result["state"] = "connected"
            result["reply"] = data.hex().upper()
        except OSError as exc:
            result["state"] = "fd-io-error"
            result["detail"] = repr(exc)
        finally:
            loop.quit()
    return inner


def main() -> None:
    conn = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    node = Gio.DBusNodeInfo.new_for_xml(PROFILE_XML)
    reg_id = conn.register_object(
        "/bp1pro/clientprofile",
        node.lookup_interface("org.bluez.Profile1"),
        on_call, None, None)
    if not reg_id:
        print("ERROR: register_object failed")
        return

    pm = Gio.DBusProxy.new_sync(
        conn, Gio.DBusProxyFlags.NONE, None, "org.bluez",
        "/org/bluez", "org.bluez.ProfileManager1", None)

    try:
        opts = {"Name": GLib.Variant("s", "bp1pro")}
        role = sys.argv[2] if len(sys.argv) > 2 else "client"
        if role == "server":
            opts["Role"] = GLib.Variant("s", "server")
        else:
            opts["Role"] = GLib.Variant("s", "client")
        pm.call_sync(
            "RegisterProfile",
            GLib.Variant("(osa{sv})", (
                "/bp1pro/clientprofile", UUID_SPP, opts)),
            Gio.DBusCallFlags.NONE, 5000, None)
        print("RegisterProfile: OK (role=%s)" % role)
        if role == "server":
            print("waiting up to 90s for the earbuds to dial our SPP server…")
    except GLib.Error as exc:
        print(f"RegisterProfile failed: {exc}")
        loop.quit()

    dev = Gio.DBusProxy.new_sync(
        conn, Gio.DBusProxyFlags.NONE, None, "org.bluez",
        DEVICE_PATH, "org.bluez.Device1", None)

    role = sys.argv[2] if len(sys.argv) > 2 else "client"
    if role != "server":  # in server mode the earbuds dial us; just wait

        def finish(_proxy, task):
            try:
                _proxy.call_finish(task)
                print("ConnectProfile: call finished")
            except GLib.Error as exc:
                print(f"ConnectProfile failed: {str(exc)}")
                if result["state"] == "init":
                    result["state"] = "connectprofile-failed"
                    result["detail"] = str(exc)
                loop.quit()

        dev.call("ConnectProfile", GLib.Variant("(s)", (UUID_SPP,)),
                 Gio.DBusCallFlags.NONE, 20000, None, finish)

    def watchdog():
        if result["state"] in ("init", "fd-received"):
            print("watchdog fired; state:", result["state"])
        loop.quit()
        return False

    GLib.timeout_add_seconds(30 if role != "server" else 90, watchdog)
    loop.run()
    print("RESULT:", result)

    try:
        pm.call_sync(
            "UnregisterProfile",
            GLib.Variant("(o)", ("/bp1pro/clientprofile",)),
            Gio.DBusCallFlags.NONE, 3000, None)
    except (GLib.Error, TypeError):
        pass


if __name__ == "__main__":
    main()
