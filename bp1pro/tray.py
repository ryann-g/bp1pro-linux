"""StatusNotifierItem (AppIndicator) tray icon — dbus-python backend.

PyGObject 3.48 cannot export a D-Bus object (``register_object`` vtable
callback marshalling is not available), but ``python3-dbus`` with its GLib
main loop is present on Zorin and works inside the GTK main loop, so the
item and its menu are exported with dbus-python.

Server contract evidence (the client we must satisfy):
zorin-appindicator@zorinos.com — ``statusNotifierWatcher.js:41`` registers
items at the hard-coded default path ``/StatusNotifierItem`` on the item's
own bus name; ``appIndicator.js`` reads properties via the standard
``org.freedesktop.DBus.Properties`` proxy; ``dbusMenu.js:367`` calls
``GetLayout(0, -1, ['type', 'children-display'])`` and unpacks nodes as
``[id, props, children]``, signature ``(ia{sv}av)``.
"""
from __future__ import annotations

import logging
import os

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

log = logging.getLogger(__name__)

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
SNI_PATH = "/StatusNotifierItem"
_MENU_SERIAL = 0
_LOOP_SET = False

ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

# menu ids
ROOT = 0
ID_STATUS = 1
ID_SEP1 = 2
ID_ANC = 3
ID_8D = 4
ID_SEP2 = 5
ID_RECONNECT = 6
ID_SHOW = 7
ID_QUIT = 8


def _ensure_glib_loop() -> None:
    """dbus-python must integrate with the GLib main loop exactly once."""
    global _LOOP_SET
    if not _LOOP_SET:
        DBusGMainLoop(set_as_default=True)
        _LOOP_SET = True


def _earbud_pixmap(size: int = 28) -> bytes:
    """Simple two-circle 'earbud' ARGB32 art for theme-missing icons."""
    import cairo  # pycairo, ships with PyGObject

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    r = size / 2.4
    rgb = (0.30, 0.62, 0.55)
    for cx, cy in ((size * 0.34, size * 0.40), (size * 0.70, size * 0.52)):
        cr.arc(cx, cy, r / 3.2, 0, 6.2832)
        cr.set_source_rgb(*rgb)
        cr.fill()
        cr.set_line_width(size / 14)
        cr.move_to(cx, cy + r / 3.0)
        cr.line_to(cx, min(size - 3, cy + r))
        cr.stroke()
    surf.flush()
    px = bytes(surf.get_data())  # cairo little-endian memory: B G R A
    out = bytearray()
    for i in range(0, size * size * 4, 4):
        b, g, r2, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        out += bytes((a, r2, g, b))  # IconPixmap 'ay' = flat ARGB32 bytes
    return bytes(out)


class _Item(dbus.service.Object):
    """org.kde.StatusNotifierItem object at /StatusNotifierItem.

    ``state`` (dict) is filled by the owner ``TrayIcon``; property getters
    convert it to dbus types on the fly.
    """

    def __init__(self, conn: dbus.Bus, state: dict) -> None:
        self._state = state
        super().__init__(conn=conn, object_path=SNI_PATH)

    # -- org.kde.StatusNotifierItem methods ---------------------------------

    @dbus.service.method(ITEM_IFACE, in_signature="ii")
    def ContextMenu(self, x: int, y: int) -> None:  # noqa: N802
        self._state.get("on_reconnect", lambda: None)()

    @dbus.service.method(ITEM_IFACE, in_signature="ii")
    def Activate(self, x: int, y: int) -> None:  # noqa: N802
        self._state.get("on_show", lambda: None)()

    @dbus.service.method(ITEM_IFACE, in_signature="ii")
    def SecondaryActivate(self, x: int, y: int) -> None:  # noqa: N802
        self._state.get("on_show", lambda: None)()

    @dbus.service.method(ITEM_IFACE, in_signature="is")
    def Scroll(self, delta: int, direction: str) -> None:  # noqa: N802
        if direction in ("down", "up"):
            self._state.get("on_anc_cycle", lambda: None)()

    @dbus.service.method(ITEM_IFACE)
    def XAyatanaSecondaryActivate(self, x: int) -> None:  # noqa: N802
        self._state.get("on_show", lambda: None)()

    # -- signals ------------------------------------------------------------

    @dbus.service.signal(ITEM_IFACE, signature="")
    def NewTitle(self) -> None:  # noqa: N802
        pass

    @dbus.service.signal(ITEM_IFACE, signature="")
    def NewIcon(self) -> None:  # noqa: N802
        pass

    @dbus.service.signal(ITEM_IFACE, signature="")
    def NewToolTip(self) -> None:  # noqa: N802
        pass

    @dbus.service.signal(ITEM_IFACE, signature="s")
    def NewStatus(self, status: str) -> None:  # noqa: N802
        pass

    # -- standard properties interface --------------------------------------

    def _sni_props(self) -> dict:
        st = self._state
        pixmap = st.get("pixmap") or b""
        if pixmap:
            icon_pixmap = dbus.Array(
                [dbus.Struct((dbus.Int32(28), dbus.Int32(28),
                              dbus.ByteArray(pixmap)))],
                signature="(iiay)")
        else:
            icon_pixmap = dbus.Array([], signature="(iiay)")
        return {
            "Category": dbus.String(st["category"]),
            "Id": dbus.String(st["id"]),
            "Title": dbus.String(st["title"]),
            "Status": dbus.String(st["status"]),
            "WindowId": dbus.Int32(0),
            "IconThemePath": dbus.String(""),
            "ItemIsMenu": dbus.Boolean(True),
            "IconName": dbus.String(st["icon"]),
            "IconPixmap": icon_pixmap,
            "OverlayIconName": dbus.String(""),
            "OverlayIconPixmap": dbus.Array([], signature="(iiay)"),
            "AttentionIconName": dbus.String(""),
            "AttentionIconPixmap": dbus.Array([], signature="(iiay)"),
            "AttentionMovieName": dbus.String(""),
            "ToolTip": dbus.Struct((
                dbus.String(st["title"]),
                dbus.Array([], signature="(iiay)"),
                dbus.String(st["title"]),
                dbus.String(st["tooltip"]),
            ), signature="sa(iiay)ss"),
            "Menu": dbus.ObjectPath(st["menu_path"]),
        }

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface: str, prop: str):
        props = self._sni_props()
        if interface != ITEM_IFACE or prop not in props:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                f"no such property {prop} on {interface}")
        return props[prop]

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str):
        if interface != ITEM_IFACE:
            return dbus.Dictionary({}, signature="sv")
        return dbus.Dictionary(self._sni_props(), signature="sv")

    @dbus.service.method(PROPS_IFACE, in_signature="ssv", out_signature="")
    def Set(self, interface: str, prop: str, value) -> None:
        raise dbus.exceptions.DBusException(
            "org.freedesktop.DBus.Error.PropertyReadOnly",
            "tray item properties are not writable")


class _Menu(dbus.service.Object):
    """com.canonical.dbusmenu object (flat menu, no nested submenus)."""

    def __init__(self, conn: dbus.Bus, state: dict, path: str) -> None:
        self._state = state
        super().__init__(conn=conn, object_path=path)

    @dbus.service.method(MENU_IFACE, in_signature="iias",
                         out_signature="u(ia{sv}av)")
    def GetLayout(self, parent_id: int, depth: int, wanted) -> tuple:
        items = self._state["props"]
        ch_of = self._state["children_of"]

        def node(nid: int, dep: int):
            if dep == 0:
                kids = dbus.Array([], signature="v")
            else:
                nxt = dep if dep < 0 else dep - 1
                kids = dbus.Array([node(c, nxt) for c in ch_of(nid)],
                                  signature="v")
            return dbus.Struct((dbus.Int32(nid),
                                dbus.Dictionary(items[nid], signature="sv"),
                                kids))

        return (dbus.UInt32(self._state["revision"]), node(parent_id, depth))

    @dbus.service.method(MENU_IFACE, in_signature="ias",
                         out_signature="a(ia{sv})")
    def GetGroupProperties(self, ids, wanted) -> list:
        items = self._state["props"]
        return [dbus.Struct((dbus.Int32(nid),
                             dbus.Dictionary(items[nid], signature="sv")))
                for nid in ids if nid in items]

    @dbus.service.method(MENU_IFACE, in_signature="is", out_signature="v")
    def GetProperty(self, nid: int, name: str):
        prop = self._state["props"].get(nid, {}).get(name)
        if prop is None:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                f"no such menu property {name}")
        return prop

    @dbus.service.method(MENU_IFACE, in_signature="isvu", out_signature="")
    def Event(self, nid: int, event: str, _data, _ts: int) -> None:
        if event in ("clicked", "activated"):
            self._state.get("on_menu_click", lambda i: None)(nid)

    @dbus.service.method(MENU_IFACE, in_signature="a(isvu)",
                         out_signature="ai")
    def EventGroup(self, events) -> list:
        bad = []
        for ev in events:
            if len(ev) >= 2 and ev[1] in ("clicked", "activated"):
                self._state.get("on_menu_click", lambda i: None)(ev[0])
        return bad

    @dbus.service.method(MENU_IFACE, in_signature="i", out_signature="b")
    def AboutToShow(self, nid: int) -> bool:  # noqa: N802
        return False

    @dbus.service.method(MENU_IFACE, in_signature="ai", out_signature="aiai")
    def AboutToShowGroup(self, ids) -> tuple:  # noqa: N802
        return ([], [])

    @dbus.service.signal(MENU_IFACE, signature="ui")
    def LayoutUpdated(self, revision: int, parent: int) -> None:
        pass

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str):
        if interface != MENU_IFACE:
            return dbus.Dictionary({}, signature="sv")
        return dbus.Dictionary({
            "Version": dbus.UInt32(3),
            "TextDirection": dbus.String("ltr"),
            "Status": dbus.String("normal"),
            "IconThemePath": dbus.Array([], signature="s"),
        }, signature="sv")

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface: str, prop: str):
        allp = self.GetAll(interface)
        if prop not in allp:
            raise dbus.exceptions.DBusException(
                "org.freedesktop.DBus.Error.InvalidArgs",
                f"no such property {prop} on {interface}")
        return allp[prop]

    @dbus.service.method(PROPS_IFACE, in_signature="ssv", out_signature="")
    def Set(self, interface: str, prop: str, value) -> None:
        raise dbus.exceptions.DBusException(
            "org.freedesktop.DBus.Error.PropertyReadOnly",
            "menu properties are not writable")


class TrayIcon:
    """Owns bus names, pushes menu/tooltip updates; safe-fails politely.

    ``actions`` maps fixed menu ids to zero-arg callables run on the GTK
    main loop. Call :meth:`render` whenever state changes.
    """

    def __init__(self, actions: dict[int, object],
                 theme_icon: str = "bp1pro") -> None:
        global _MENU_SERIAL
        _MENU_SERIAL += 1
        self._menu_path = f"/StatusNotifierItem/menu{_MENU_SERIAL}"
        self._theme_icon = theme_icon
        self._actions = dict(actions)
        self._registered = False
        self._revision = 1
        self._bus: dbus.BusInterface = None
        self._bus_name = ""
        self._name_token = None
        self._item: _Item | None = None
        self._menu: _Menu | None = None
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            has_icon = Gtk.IconTheme.get_default().has_icon(theme_icon)
        except (ImportError, ValueError, AttributeError):
            has_icon = False
        self._pixmap = b"" if has_icon else _earbud_pixmap()
        # raw python-level state shared by both exported objects
        self.state = {
            "category": "Hardware",
            "id": "bp1pro",
            "title": "BP1 Pro",
            "status": "Active",
            "icon": theme_icon,
            "tooltip": "earbuds not connected",
            "menu_path": self._menu_path,
            "pixmap": self._pixmap,
            "revision": self._revision,
            "props": {},
            "children_of": self._children,
            "on_menu_click": self._dispatch,
            "on_show": self._actions.get(ID_SHOW, lambda: None),
            "on_reconnect": self._actions.get(ID_RECONNECT, lambda: None),
            "on_anc_cycle": self._actions.get(ID_ANC, lambda: None),
            "on_quit": self._actions.get(ID_QUIT, lambda: None),
        }
        self._build_menu("Not connected", "off", "off")

    # -- public API ---------------------------------------------------------

    def start(self) -> None:
        _ensure_glib_loop()
        self._bus = dbus.SessionBus()
        serial = _MENU_SERIAL
        self._bus_name = (f"org.kde.StatusNotifierItem-{os.getpid()}"
                          f"-{serial}")
        self._name_token = dbus.service.BusName(self._bus_name, bus=self._bus)
        self._item = _Item(self._bus, self.state)
        self._menu = _Menu(self._bus, self.state, self._menu_path)
        log.info("tray item exported at %s (%s)", SNI_PATH, self._bus_name)
        self._register()

    def stop(self) -> None:
        for obj in (self._menu, self._item):
            if obj is not None:
                try:
                    obj.remove_from_connection()
                except (LookupError, dbus.exceptions.DBusException):
                    pass
        self._menu = self._item = None
        if self._name_token is not None:
            try:
                self._bus.release_name(self._bus_name)
            except dbus.exceptions.DBusException:
                pass
            self._name_token = None

    @property
    def bus_name(self) -> str:
        return self._bus_name

    @property
    def registered(self) -> bool:
        return self._registered

    def retry_registration(self) -> None:
        if self._bus is not None and not self._registered:
            self._register()

    def render(self, status_text: str, anc_label: str, eightd_label: str,
               tooltip_text: str) -> None:
        """Refresh menu labels + tooltip from current app state."""
        self.state["tooltip"] = tooltip_text
        self._build_menu(status_text, anc_label, eightd_label)
        self._revision += 1
        self.state["revision"] = self._revision
        if self._registered and self._menu is not None:
            self._menu.LayoutUpdated(dbus.UInt32(self._revision),
                                     dbus.Int32(ROOT))
            self._item.NewToolTip()
            self._item.NewIcon()

    # -- menu content -------------------------------------------------------

    def _children(self, pid: int) -> list[int]:
        if pid == ROOT:
            return [ID_STATUS, ID_SEP1, ID_ANC, ID_8D,
                    ID_SEP2, ID_RECONNECT, ID_SHOW, ID_QUIT]
        return []

    def _build_menu(self, status_text: str, anc_label: str,
                    eightd_label: str) -> None:
        # dbus-python 1.3 has no constructible Variant; 'sv' and 'av' slots
        # auto-wrap plain typed values into variants on the wire
        def v(text):
            return dbus.String(text)

        self.state["props"] = {
            ROOT: {"type": v("root"), "children-display": v("submenu")},
            ID_STATUS: {"type": v("standard"), "label": v(status_text),
                        "enabled": dbus.Boolean(False)},
            ID_SEP1: {"type": v("separator")},
            ID_ANC: {"type": v("standard"),
                     "label": v(f"ANC mode: {anc_label}"),
                     "enabled": dbus.Boolean(True)},
            ID_8D: {"type": v("standard"),
                    "label": v(f"8D (spatial): {eightd_label}"),
                    "enabled": dbus.Boolean(True)},
            ID_SEP2: {"type": v("separator")},
            ID_RECONNECT: {"type": v("standard"), "label": v("Reconnect"),
                           "enabled": dbus.Boolean(True)},
            ID_SHOW: {"type": v("standard"), "label": v("Show window"),
                      "enabled": dbus.Boolean(True)},
            ID_QUIT: {"type": v("standard"), "label": v("Quit"),
                      "enabled": dbus.Boolean(True)},
        }

    # -- registration + dispatch --------------------------------------------

    def _register(self) -> None:
        try:
            watcher = self._bus.get_object(
                WATCHER_NAME, "/StatusNotifierWatcher", introspect=False)
            watcher.RegisterStatusNotifierItem(
                self._bus_name, dbus_interface=WATCHER_NAME, timeout=5.0)
        except dbus.exceptions.DBusException as exc:
            log.warning(
                "tray icon not registered (%s) — no StatusNotifier host "
                "(enable one: gnome-extensions enable "
                "zorin-appindicator@zorinos.com)", exc)
            self._registered = False
            return
        self._registered = True
        log.info("tray icon registered with StatusNotifierWatcher")
        if self._menu is not None:
            self._menu.LayoutUpdated(dbus.UInt32(self._revision),
                                     dbus.Int32(ROOT))

    def _dispatch(self, nid: int) -> None:
        self._actions.get(nid, lambda: None)()
