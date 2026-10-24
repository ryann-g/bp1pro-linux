"""GTK3 desktop GUI for the Baseus BP1 Pro (Linux).

All RFCOMM work runs in worker threads; UI updates are marshalled back
through ``GLib.idle_add``. Exchanges are logged to a TextView pane. The
app is started as a normal desktop application (bp1pro.desktop) — no
terminal required.
"""

from __future__ import annotations

import json
import logging
import pathlib
import threading
from typing import Callable, Optional

import gi  # noqa: F401
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from . import device, protocol
from .transport import BaseTransport, MockTransport, RfcommTransport, TransportError

log = logging.getLogger(__name__)

CONFIG_PATH = pathlib.Path.home() / ".config" / "bp1pro.json"
CLICKS = ("single", "double", "triple", "long")
SIDES = ("left", "right", "both")

ON_DONE = Callable[[str, list[str], Optional[str]], None]


class Gui(Gtk.Window):
    """Main window — menu of controls + TX/RX log."""

    def __init__(self, mock: bool = False) -> None:
        super().__init__(title="Baseus BP1 Pro Control")
        Gtk.Window.set_default_icon_name("bp1pro")
        self.set_default_size(760, 580)
        self.mock = mock
        self.tr: Optional[BaseTransport] = None
        self.config = self._load_config()
        self._busy = False
        self._auto_connect_attempted = False
        self._pending: Optional[tuple[str, list[tuple[str, str | None]]]] = None
        self._after_conn: Optional[tuple[str, Optional[int]]] = None
        # GLib source id of the post-connect status poll (battery refresher)
        self._poll_tag: Optional[int] = None
        # last-known battery percentages; AA27 (case) is push-only
        self.batt = {"left": None, "right": None, "case": None}

        header = Gtk.HeaderBar(title="Baseus BP1 Pro Control",
                               subtitle=self.config.get("addr", "not set"))
        header.set_show_close_button(True)
        self.headerbar = header
        self.set_titlebar(header)

        self.reconnect_btn = Gtk.Button(label="Connect")
        self.reconnect_btn.connect("clicked", lambda w: self.on_connect())
        header.pack_end(self.reconnect_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer)

        self.infobar = Gtk.InfoBar()
        self.infobar.set_message_type(Gtk.MessageType.ERROR)
        self.infobar.set_show_close_button(True)
        self.infobar.set_no_show_all(True)
        self.info_label = Gtk.Label(label="")
        self.info_label.set_line_wrap(True)
        area = self.infobar.get_content_area()
        area.add(self.info_label)
        self.infobar.connect("response", self._infobar_response)
        outer.pack_start(self.infobar, False, False, 0)

        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        outer.pack_start(self.notebook, True, True, 0)

        self.notebook.append_page(self._page_device(), Gtk.Label(label="Device"))
        self.notebook.append_page(self._page_anc(), Gtk.Label(label="Noise control"))
        self.notebook.append_page(self._page_eq(), Gtk.Label(label="EQ"))
        self.notebook.append_page(self._page_spatial(),
                                  Gtk.Label(label="8D / spatial"))
        self.notebook.append_page(self._page_gestures(), Gtk.Label(label="Gestures"))
        self.notebook.append_page(self._page_log(), Gtk.Label(label="Log"))

        self.status = Gtk.Statusbar()
        self.ctx = self.status.get_context_id("bp1pro")
        self.statusmsg("starting…")
        GLib.idle_add(lambda: (self._refresh_devices_async(), False)[1])
        self.connect("destroy", self._on_destroy)

    # -- pages ------------------------------------------------------------

    def _page_device(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      border_width=12)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label="Earbud:"), False, False, 0)
        self.addr_combo = Gtk.ComboBoxText()
        self.addr_combo.pack_start(Gtk.CellRendererText(), True)
        row.pack_start(self.addr_combo, True, True, 0)
        self.device_refresh = Gtk.Button(label="Refresh")
        self.device_refresh.connect("clicked",
                                    lambda w: self._refresh_devices_async())
        row.pack_end(self.device_refresh, False, False, 0)
        box.pack_start(row, False, False, 0)

        opts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.force_channel = Gtk.Entry()
        self.force_channel.set_placeholder_text("RFCOMM channel (blank = scan)")
        opts.pack_start(Gtk.Label(label="Channel:"), False, False, 0)
        opts.pack_start(self.force_channel, False, False, 0)
        self.mock_switch = Gtk.Switch(active=self.mock)
        mock_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mock_row.pack_start(Gtk.Label(label="Mock earbud (demo)"), False, False, 0)
        mock_row.pack_start(self.mock_switch, False, False, 0)
        opts.pack_end(mock_row, False, False, 0)
        box.pack_start(opts, False, False, 0)

        # persistent battery row: AA02 is pushed on connect/exchanges; the
        # case level (AA27) arrives only as a spontaneous push, no query
        # frame exists in the app (HomeBleDataResolvePresenter.c0:1045).
        self.batt_lbl = Gtk.Label()
        self._render_battery()
        box.pack_start(self.batt_lbl, False, False, 0)

        quit_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        close_link = Gtk.Button(label="Disconnect & close link")
        close_link.connect("clicked", lambda w: self._close_link())
        quit_row.pack_start(close_link, False, False, 0)
        box.pack_start(quit_row, False, False, 0)

        help = Gtk.Label()
        help.set_markup(
            "<b>Pair first</b>, once: bluetoothctl → power on → scan on →"
            " pair &lt;MAC&gt; → trust &lt;MAC&gt; → connect &lt;MAC&gt;.\n"
            "Then leave the buds connected; music runs via the normal A2DP"
            " audio stack, this app talks control on top.")
        help.set_line_wrap(True)
        box.pack_start(help, False, False, 12)
        return box

    def _page_anc(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      border_width=12)
        self.anc_combo = Gtk.ComboBoxText()
        for code, name in sorted(protocol.ANC_MODES.items()):
            self.anc_combo.append(str(code), name)
        self.anc_combo.set_active(1)
        box.pack_start(Gtk.Label(label="Mode"), False, False, 0)
        box.pack_start(self.anc_combo, False, False, 0)

        lvl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.level_check = Gtk.CheckButton(label="strength")
        self.level_check.set_active(True)
        lvl_row.pack_start(self.level_check, False, False, 0)
        self.level_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                    0, 100, 1)
        self.level_scale.set_value(40)
        lvl_row.pack_start(self.level_scale, True, True, 0)
        box.pack_start(lvl_row, False, False, 0)

        box.pack_start(self._apply_button(self.on_anc), False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 4)

        self.inear_switch = self._toggle("In-ear detection (auto pause)",
                                         lambda on: protocol.inear_set(on))
        self.lowlat_switch = self._toggle("Low latency / game audio mode",
                                          lambda on: protocol.mode_set(on))
        box.pack_start(self.inear_switch[0], False, False, 0)
        box.pack_start(self.lowlat_switch[0], False, False, 0)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_row.pack_start(Gtk.Label(label="Current state:"), False, False, 0)
        self.anc_state = Gtk.Label(label="?")
        status_row.pack_start(self.anc_state, False, False, 0)
        box.pack_start(status_row, False, False, 6)
        box.pack_start(Gtk.Separator(), False, False, 4)
        box.pack_start(Gtk.Label(
            label="AA34 0C = enabling needed first · 0D = can't set in call · "
                  "00 = wear the buds and retry"), False, False, 0)
        return box

    def _page_eq(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      border_width=12)
        self.eq_combo = Gtk.ComboBoxText()
        self._reload_eq_combo()
        box.pack_start(Gtk.Label(
            label="Equalizer preset. The firmware only knows numeric ids "
                  "(wire BA31 <id>, ack AA30); preset names are cloud config "
                  "inside the official app, so here they are local and "
                  "editable below."), False, False, 0)
        box.pack_start(self.eq_combo, False, False, 0)
        box.pack_start(self._apply_button(self.on_eq), False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 4)
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_row.pack_start(Gtk.Label(label="Label:"), False, False, 0)
        self.eq_name_entry = Gtk.Entry()
        self.eq_name_entry.set_placeholder_text(
            "name for the selected preset")
        self.eq_name_btn = Gtk.Button(label="Save name")
        self.eq_name_btn.connect("clicked", lambda w: self.on_eq_name())
        name_row.pack_start(self.eq_name_entry, True, True, 0)
        name_row.pack_end(self.eq_name_btn, False, False, 0)
        box.pack_start(name_row, False, False, 0)
        return box

    def _reload_eq_combo(self) -> None:
        """(Re)populate the EQ combo from the local name map."""
        names = protocol.load_eq_names()
        current = self.eq_combo.get_active_id()
        self.eq_combo.remove_all()
        for fid in range(16):
            self.eq_combo.append(str(fid), f"{fid} · {protocol.eq_label(fid, names)}")
        if current is not None:
            self.eq_combo.set_active_id(current)
        else:
            self.eq_combo.set_active(0)

    def _page_spatial(self) -> Gtk.Widget:
        """8D / spatial ("panoramic") sound tab — a DSP chain, wired with
        BA42 query / BA43 set (PanoramicSoundViewModel.java:104/:132)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      border_width=12)
        self.spatial_combo = Gtk.ComboBoxText()
        # BP1 Pro supports off/music/cinema only (DeviceManager h0/c1/d1/g0
        # + PanoramicSoundViewModel.A() all false for this model)
        for mode in (0, 1, 2):
            self.spatial_combo.append(str(mode), protocol.SPATIAL_MODES[mode])
        self.spatial_combo.set_active(0)
        box.pack_start(Gtk.Label(label="8D (spatial / panoramic) sound"),
                       False, False, 0)
        box.pack_start(self.spatial_combo, False, False, 0)
        box.pack_start(self._apply_button(self.on_spatial), False, False, 0)
        state_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        state_row.pack_start(Gtk.Label(label="Current state:"), False, False, 0)
        self.spatial_state = Gtk.Label(label="?")
        state_row.pack_start(self.spatial_state, False, False, 0)
        box.pack_start(state_row, False, False, 6)
        box.pack_start(Gtk.Label(
            label="Separate DSP chain from the EQ. Rejected with AA43 0A/0B "
                  "when LHDC/LDAC is active, 0C until both buds are "
                  "connected, 0D during a call."), False, False, 0)
        return box

    def _page_gestures(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      border_width=12)
        self.click_combo = Gtk.ComboBoxText()
        for name in CLICKS:
            self.click_combo.append_text(name)
        self.click_combo.set_active(0)
        self.side_combo = Gtk.ComboBoxText()
        for name in SIDES:
            self.side_combo.append_text(name)
        self.side_combo.set_active(0)
        self.func_combo = Gtk.ComboBoxText()
        for fid, label in sorted(protocol.FUNCTIONS.items()):
            self.func_combo.append(str(fid), f"{fid:02d} · {label}")
        self.func_combo.set_active(0)

        for title, widget in (("Click", self.click_combo),
                              ("Side", self.side_combo),
                              ("Function", self.func_combo)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(Gtk.Label(label=title), False, False, 2)
            row.pack_start(widget, True, True, 0)
            box.pack_start(row, False, False, 0)
        box.pack_start(self._apply_button(self.on_gesture), False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 4)
        read_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.read_btn = Gtk.SpinButton.new_with_range(0, 63, 1)
        btn = Gtk.Button(label="Read button…")
        btn.connect("clicked", lambda w: self.on_gesture_query())
        read_row.pack_start(Gtk.Label(label="Query button id:"), False, False, 0)
        read_row.pack_start(self.read_btn, False, False, 0)
        read_row.pack_end(btn, False, False, 0)
        box.pack_start(read_row, False, False, 0)
        return box

    def _page_log(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.log_view.set_monospace(True)
        self.log_buffer = self.log_view.get_buffer()
        scroll.add(self.log_view)
        return scroll

    def _apply_button(self, handler: Callable[[], None]) -> Gtk.Button:
        btn = Gtk.Button(label="Apply")
        btn.get_style_context().add_class("suggested-action")
        btn.connect("clicked", lambda w: handler())
        return btn

    def _toggle(self, label: str, build: Callable[[bool], str]):
        switch = Gtk.Switch()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label=label), False, False, 0)
        row.pack_end(switch, False, False, 0)
        switch.connect("state-set",
                       lambda w, state: self._send_toggle(build, state))
        return row, switch

    # -- config -----------------------------------------------------------

    @staticmethod
    def _load_config() -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (OSError, ValueError):
            return {}

    def _save_config(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # merge into the on-disk file: protocol.save_eq_name also writes here
        try:
            data = json.loads(CONFIG_PATH.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.update(self.config)
        CONFIG_PATH.write_text(json.dumps(data, indent=1))

    # -- helper widgets & plumbing -----------------------------------------

    def statusmsg(self, text: str) -> None:
        self.status.pop(self.ctx)
        self.status.push(self.ctx, text)

    def _render_battery(self) -> None:
        """Device-page battery row; '–' until a frame reports the level."""
        def pct(level: Optional[int]) -> str:
            return "–" if level is None else f"{level}%"
        self.batt_lbl.set_text(
            f"Battery: L {pct(self.batt['left'])} · R {pct(self.batt['right'])}"
            f" · case {pct(self.batt['case'])}")

    def append_log(self, line: str) -> None:
        self.log_buffer.insert(self.log_buffer.get_end_iter(), line + "\n")

    def _close_link(self) -> None:
        if self._poll_tag is not None:
            GLib.source_remove(self._poll_tag)
            self._poll_tag = None
        if self.tr is not None:
            try:
                self.tr.close()
            finally:
                self.tr = None
        self.statusmsg("disconnected")

    def _on_destroy(self, *_args: object) -> None:
        self._close_link()

    # -- async worker pattern ----------------------------------------------

    def _async(self, label: str, fn: Callable[[], list[str]],
               quiet: bool = False) -> None:
        """Run ``fn`` (transport chattiness) off the UI thread.

        ``quiet``: poll/background exchanges log their frames but never
        raise banners or rewrite the status bar.
        """
        if self._busy:
            self.append_log(f"!! {label}: dialogue busy — wait a moment")
            self.notify_error(f"{label}: another action is running — wait a moment")
            return

        def run() -> None:
            self._busy = True
            try:
                frames = fn()
            except TransportError as exc:
                GLib.idle_add(self._on_done, label, [], str(exc), quiet)
                return
            except (OSError, ValueError, AttributeError) as exc:
                GLib.idle_add(self._on_done, label, [], repr(exc), quiet)
                return
            GLib.idle_add(self._on_done, label, frames, None, quiet)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, label: str, frames: list[str],
                 error: Optional[str], quiet: bool = False) -> None:
        self._busy = False
        if error:
            self.append_log(f"!! {label}: {error}")
            if not quiet:  # polls fail quietly (buds away / link gone)
                self.statusmsg(f"{label}: error")
                self.notify_error(f"{label} failed: {error}")
            return
        for f in frames:
            if protocol.is_frame(f):
                self.append_log(f"RX {f}  ({protocol.decode(f)})")
            else:  # status/annotation lines from connect flow
                self.append_log(str(f))
        self._mirrors_state(frames)
        if not quiet:
            if label.startswith("connecting"):
                self.statusmsg("connected" if self.tr else "connect failed")
            else:
                self.statusmsg(f"{label} — done")
        # continuation work queued by the connect flow: runs here, after
        # _busy cleared, so its status burst cannot be dropped as "busy"
        after, self._after_conn = self._after_conn, None
        if after:
            self._after_connect(*after)

    def _infobar_response(self, bar: Gtk.InfoBar, response: int) -> None:
        bar.set_revealed(False)

    def notify_error(self, text: str) -> None:
        """Show an error banner (dismissable, auto-hides)."""
        self.info_label.set_text(text)
        self.infobar.set_revealed(True)
        if getattr(self, "_info_timer", None):
            GLib.source_remove(self._info_timer)
        self._info_timer = GLib.timeout_add_seconds(9, self._hide_info)
        self.append_log(f"! {text}")

    def _hide_info(self) -> bool:
        self.infobar.set_revealed(False)
        return False

    def _mirrors_state(self, frames: list[str]) -> None:
        """Update on-screen state from received frames."""
        for frame in frames:
            if frame.startswith("AA33") and len(frame) >= 8:
                mode, level = int(frame[4:6], 16), int(frame[6:8], 16)
                name = protocol.ANC_MODES.get(mode, f"mode {mode}")
                level_text = (f" level {level}" if level != protocol.UNSET
                              else "")
                self.anc_state.set_text(f"{name}{level_text}")
                combo_id = self.anc_combo.get_active_id()
                if combo_id != str(mode):
                    self.anc_combo.set_active(mode)
            elif frame.startswith("AA02") and len(frame) >= 12:
                try:
                    self.batt["left"] = int(frame[4:6], 16)
                    self.batt["right"] = int(frame[8:10], 16)
                except ValueError:
                    continue
                self._render_battery()
                self.statusmsg(f"battery: left {self.batt['left']}% · "
                               f"right {self.batt['right']}%")
            elif frame.startswith("AA27") and len(frame) >= 6:
                try:
                    self.batt["case"] = int(frame[4:6], 16)
                except ValueError:
                    continue
                self._render_battery()
            elif frame.startswith("AA30") and len(frame) >= 6:
                try:
                    self.eq_combo.set_active_id(str(int(frame[4:6], 16)))
                except ValueError:
                    continue
            elif frame.startswith("AA42") and len(frame) >= 6:
                try:
                    mode = int(frame[4:6], 16)
                except ValueError:
                    continue
                self.spatial_state.set_text(
                    protocol.SPATIAL_MODES.get(mode, f"state 0x{frame[4:6]}"))
                if mode in (0, 1, 2) \
                        and self.spatial_combo.get_active_id() != str(mode):
                    self.spatial_combo.set_active_id(str(mode))

    # -- actions -----------------------------------------------------------

    def _resolve_channel(self) -> Optional[int]:
        text = self.force_channel.get_text().strip()
        return int(text, 0) if text else None

    def on_connect(self) -> None:
        if self.tr is not None:
            self._close_link()
        addr = self.addr_combo.get_active_text()
        if not addr:
            self.addr_combo.set_active(0)  # try first entry
            addr = self.addr_combo.get_active_text()
        if not addr:
            self.statusmsg("no device selected — Refresh first")
            return
        if self.mock_switch.get_active():
            self.tr = MockTransport(addr)
            self.tr.connect()
            self.headerbar.set_subtitle(f"{addr} [mock]")
            self.statusmsg("mock earbud ready")
            self._save_addr(addr)
            pending, self._pending = self._pending, None
            if pending:
                label, sequence = pending
                self._send_many(label, sequence)
            else:
                self._send_many("status", [
                    (protocol.anc_query(), "AA33"),
                    (protocol.spatial_query(), "AA42"),
                ])
            return
        channel = None
        try:
            channel = self._resolve_channel()
        except ValueError:
            self.append_log("!! channel must be a number")
        self.statusmsg("connecting… (takes a few seconds — keep buds worn)")
        self._async(f"connecting {addr}", self._do_connect(addr, channel))

    def _do_connect(self, addr: str, channel: Optional[int]) -> Callable[[],
                    list[str]]:

        def run() -> list[str]:
            if not device.ensure_connected(addr):
                raise TransportError(
                    "bluetoothctl reports the buds not connected; they are "
                    "probably asleep in the case — wear them / open the case, "
                    "then press Connect again")
            tr = RfcommTransport(addr, channel=channel)
            tr.connect()
            self.tr = tr  # swap only after success
            self.config["addr"] = addr
            if tr.channel is not None:
                self.config["ch"] = tr.channel
            # the after-connect work (status/battery burst) must run on the
            # main thread AFTER _on_done clears _busy — the previous
            # idle_add from this worker thread won that race and the
            # status burst was dropped by the busy gate on every real
            # connect ("dialogue busy")
            self._after_conn = (addr, tr.channel)
            return [f"connect {addr} on RFCOMM channel {tr.channel}",
                    "  (control link open; audio keeps streaming via A2DP)"]

        return run

    def _after_connect(self, addr: str, channel: Optional[int]) -> bool:
        self.headerbar.set_subtitle(f"{addr}{'' if channel is None else f' · ch{channel}'}")
        self._save_config()
        pending, self._pending = self._pending, None
        if pending is None:
            # fresh link: the buds answer with ANC + 8D + battery burst
            self._send_many("status", [
                (protocol.anc_query(), "AA33"),
                (protocol.spatial_query(), "AA42"),
            ])
        else:
            label, sequence = pending
            self._send_many(label, sequence)
        if self._poll_tag is None:
            # raw evidence: the buds push battery (AA02/AA27) ~1.5 s after
            # link open — between exchanges — and a BA33 query alone pulls
            # no battery.  A slow poll drains whatever the buds queued and
            # keeps the row + mirror state fresh for later drifts.
            self._poll_tag = GLib.timeout_add_seconds(4, self._poll_tick)
        return False

    def _poll_tick(self) -> bool:
        """Quiet status poll: reads buffered pushes, refreshes mirrors."""
        if self.tr is None or not self.tr.connected:
            self._poll_tag = None
            return False  # self-cancel; the next successful connect restarts
        if self._busy:
            return True  # an action owns the dialogue — skip this beat
        self._send_many("autorefresh", [
            (protocol.anc_query(), "AA33"),
        ], quiet=True)
        return True

    def _save_addr(self, addr: str) -> None:
        self.config["addr"] = addr
        self._save_config()

    def _send(self, label: str, frame: str, expect: Optional[str] = None) -> None:
        self._send_many(label, [(frame, expect)])

    def _send_many(self, label: str,
                   sequence: list[tuple[str, Optional[str]]],
                   quiet: bool = False) -> None:
        if self.tr is None:
            self._pending = (label, list(sequence))
            self.notify_error(
                "not connected — connecting now; your action runs as soon as"
                " the link is up. Keep the buds worn / out of the case.")
            self.on_connect()
            return

        def run() -> list[str]:
            # raw frames + "TX …" markers; _on_done prints frame lines with
            # their decode and passes markers through verbatim, while the
            # state-mirror logic matches only the raw hex frames
            out: list[str] = []
            for frame, expect in sequence:
                out.append(f"TX {frame}")
                got = self.tr.exchange(frame, expect=expect)
                out.extend(got)
            return out

        self._async(label, run, quiet=quiet)

    def on_anc(self) -> None:
        mode_id = self.anc_combo.get_active_id()
        if mode_id is None:
            return
        level = int(self.level_scale.get_value()) if self.level_check.get_active() \
            else None
        self._send_many("ANC", [
            (protocol.anc_set(int(mode_id), level), "AA34"),
            (protocol.anc_query(), "AA33"),
        ])

    def on_eq(self) -> None:
        fid = self.eq_combo.get_active_id()
        if fid is not None:
            self._send("EQ", protocol.eq_select(int(fid)), expect="AA30")

    def on_eq_name(self) -> None:
        fid = self.eq_combo.get_active_id()
        label = self.eq_name_entry.get_text().strip()
        if fid is None or not label:
            self.notify_error("pick a preset and type a name first")
            return
        protocol.save_eq_name(int(fid), label)
        self._reload_eq_combo()
        self.eq_name_entry.set_text("")
        self.statusmsg(f"EQ preset {fid} named '{label}'")

    def on_spatial(self) -> None:
        mid = self.spatial_combo.get_active_id()
        if mid is None:
            return
        self._send_many("8D spatial", [
            (protocol.spatial_set(int(mid)), "AA43"),
            (protocol.spatial_query(), "AA42"),
        ])

    def on_gesture(self) -> None:
        click = self.click_combo.get_active_text()
        side = self.side_combo.get_active_text()
        fid = self.func_combo.get_active_id()
        if click and side and fid is not None:
            self._send("gesture", protocol.gesture_set(click, int(fid), side))

    def on_gesture_query(self) -> None:
        self._send("gesture read", protocol.gesture_query(int(self.read_btn.get_value())))

    def _send_toggle(self, build: Callable[[bool], str], state: bool) -> bool:
        label = "toggle"
        if self.tr is None:
            self.notify_error("not connected yet — press Connect first; the "
                              "switch was not applied")
            return True  # revert the switch
        self._send(label, build(state))
        return False

    # -- device discovery ---------------------------------------------------

    def _refresh_devices_async(self) -> bool:
        self.device_refresh.set_sensitive(False)

        def run() -> None:
            devices = device.find_baseus()
            GLib.idle_add(self._fill_devices, devices)

        threading.Thread(target=run, daemon=True).start()
        return False

    def _fill_devices(self, devices: list[tuple[str, str]]) -> bool:
        self.device_refresh.set_sensitive(True)
        self.addr_combo.remove_all()
        current = self.config.get("addr", "")
        seen = set()
        for addr, name in devices:
            if addr not in seen:
                self.addr_combo.append_text(addr)
                seen.add(addr)
        if self.mock_switch.get_active() and not seen:
            self.addr_combo.append_text("00:11:22:33:44:55")
            seen.add("00:11:22:33:44:55")
        if current and current in seen:
            self.addr_combo.set_active(list(seen).index(current))
        else:
            self.addr_combo.set_active(0 if seen else -1)
        if seen:
            self.headerbar.set_subtitle(self.addr_combo.get_active_text())
            if self.tr is None and not self._auto_connect_attempted:
                self._auto_connect_attempted = True
                self.statusmsg("auto-connecting…")
                self.on_connect()
            else:
                self.statusmsg(f"{len(seen)} device(s)* paired")
        else:
            self.statusmsg("no Baseus device paired — see the Device tab")
        return False


def run(mock: bool = False) -> int:
    gui = Gui(mock=mock)
    gui.show_all()
    gui.connect("delete-event", Gtk.main_quit)
    Gtk.main()
    return 0
