"""Panel tray glue for the GTK app.

Mixed into :class:`bp1pro.gui.Gui` — builds the StatusNotifierItem icon,
renders app state into its menu, and routes tray actions back to widgets.
Kept in its own file so the GUI module stays within the 800-line cap.
"""
from __future__ import annotations

import logging

from gi.repository import GLib

from . import protocol
from . import tray as tray_mod

log = logging.getLogger(__name__)


class TrayMixin:
    """Tray icon integration; requires the attributes of ``Gui``."""

    tray: object | None

    # -- panel tray icon (StatusNotifierItem) --------------------------------

    def _make_tray(self) -> None:
        """Panel icon via a dbus-python StatusNotifierItem.

        Shown by any SN host (Zorin AppIndicators, KDE, XFCE...); if none is
        present the GUI runs fine without the icon and retries later.
        """
        try:
            self.tray = tray_mod.TrayIcon(actions={
                tray_mod.ID_ANC: self._tray_cycle_anc,
                tray_mod.ID_8D: self._tray_cycle_8d,
                tray_mod.ID_RECONNECT: self.on_connect,
                tray_mod.ID_SHOW: self._tray_present,
                tray_mod.ID_QUIT: self.close,
            })
            self.tray.start()
            # poll until registered so "enable the extension later" works
            GLib.timeout_add_seconds(15, self._tray_retry_tick)
            self._tray_render()
        except Exception:  # noqa: BLE001 — tray must never block the GUI
            log.exception("tray icon init failed — running without one")
            self.tray = None

    def _tray_retry_tick(self) -> bool:
        if self.tray is None or self.tray.registered:
            return False
        self.tray.retry_registration()
        return True

    def _tray_render(self) -> None:
        """Push connection/battery/ANC/8D state into the tray menu."""
        if self.tray is None:
            return
        connected = self.tr is not None and self.tr.connected
        if connected:
            status = self.batt_lbl.get_text().replace("Battery: ", "")
            tooltip = f"BP1 Pro — {status}"
        else:
            status = "Not connected"
            tooltip = "BP1 Pro earbuds not connected"
        anc_id = self.anc_combo.get_active_id()
        anc_label = protocol.ANC_MODES.get(
            int(anc_id), "–") if anc_id is not None else "–"
        eightd_id = self.spatial_combo.get_active_id()
        eightd_label = protocol.SPATIAL_MODES.get(
            int(eightd_id), "–") if eightd_id is not None else "–"
        try:
            self.tray.render(status, anc_label, eightd_label, tooltip)
        except Exception:  # noqa: BLE001
            log.exception("tray render failed")

    def _tray_cycle_anc(self) -> None:
        cur = self.anc_combo.get_active_id()
        nxt = (int(cur) + 1) % len(protocol.ANC_MODES) if cur is not None else 1
        self.anc_combo.set_active_id(str(nxt))
        self.on_anc()

    def _tray_cycle_8d(self) -> None:
        cur = self.spatial_combo.get_active_id()
        nxt = (int(cur) + 1) % len(protocol.SPATIAL_MODES) \
            if cur is not None else 0
        self.spatial_combo.set_active_id(str(nxt))
        self.on_spatial()

    def _tray_present(self) -> None:
        self.present()
        self.notebook.set_current_page(0)
