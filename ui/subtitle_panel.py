from __future__ import annotations

from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class SubtitlePanel(Gtk.Frame):
    """Subtitle controls: enable checkbox and track selector."""

    def __init__(self):
        super().__init__(label="Subtitles")

        self._enable_check: Optional[Gtk.CheckButton] = None
        self._track_combo: Optional[Gtk.ComboBoxText] = None

        self._on_enable_changed: Optional[Callable[[bool], None]] = None
        self._on_track_changed: Optional[Callable[[int], None]] = None

        self._build()

    # ------------------------------------------------------------------
    # Callback setters
    # ------------------------------------------------------------------

    def set_enable_callback(self, cb: Optional[Callable[[bool], None]]) -> None:
        self._on_enable_changed = cb

    def set_track_callback(self, cb: Optional[Callable[[int], None]]) -> None:
        self._on_track_changed = cb

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def clear_tracks(self) -> None:
        if self._track_combo:
            self._track_combo.remove_all()

    def add_track(self, label: str, index: int) -> None:
        if self._track_combo:
            self._track_combo.append(str(index), label)

    def set_selected_track(self, index: int) -> None:
        if self._track_combo:
            self._track_combo.set_active_id(str(index))

    def get_selected_track(self) -> int:
        if self._track_combo:
            active_id = self._track_combo.get_active_id()
            if active_id is not None:
                return int(active_id)
        return 0

    def set_enabled(self, enabled: bool) -> None:
        if self._enable_check:
            self._enable_check.set_active(enabled)
        if self._track_combo:
            self._track_combo.set_sensitive(enabled)

    def get_enabled(self) -> bool:
        if self._enable_check:
            return self._enable_check.get_active()
        return False

    def set_controls_sensitive(self, sensitive: bool) -> None:
        if self._enable_check:
            self._enable_check.set_sensitive(sensitive)

    # ------------------------------------------------------------------
    # Block signals helper
    # ------------------------------------------------------------------

    def block_signals(self, blocked: bool = True) -> None:
        pairs = [
            (self._enable_check, self._on_enable_toggled),
            (self._track_combo, self._on_track_combo_changed),
        ]
        for widget, handler in pairs:
            if widget is not None:
                try:
                    if blocked:
                        widget.handler_block_by_func(handler)
                    else:
                        widget.handler_unblock_by_func(handler)
                except TypeError:
                    pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        self.add(box)

        self._enable_check = Gtk.CheckButton(label="Enable")
        self._enable_check.connect("toggled", self._on_enable_toggled)
        box.pack_start(self._enable_check, False, False, 0)

        box.pack_start(Gtk.Label(label="Track:"), False, False, 0)

        self._track_combo = Gtk.ComboBoxText()
        self._track_combo.set_hexpand(True)
        self._track_combo.connect("changed", self._on_track_combo_changed)
        self._track_combo.set_sensitive(False)
        box.pack_start(self._track_combo, True, True, 0)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_enable_toggled(self, check: Gtk.CheckButton) -> None:
        enabled = check.get_active()
        if self._track_combo:
            self._track_combo.set_sensitive(enabled)
        if self._on_enable_changed:
            self._on_enable_changed(enabled)

    def _on_track_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._on_track_changed and combo.get_active_iter():
            self._on_track_changed(self.get_selected_track())
