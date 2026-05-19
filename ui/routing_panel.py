from __future__ import annotations

from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class RoutingPanel(Gtk.Frame):
    """Per-listener controls: audio track, output device, volume, delay."""

    def __init__(self, listener_id: str, label: str):
        super().__init__(label=label)
        self._listener_id = listener_id

        self._track_combo: Optional[Gtk.ComboBoxText] = None
        self._sink_combo: Optional[Gtk.ComboBoxText] = None
        self._volume_scale: Optional[Gtk.Scale] = None
        self._delay_spin: Optional[Gtk.SpinButton] = None
        self._refresh_button: Optional[Gtk.Button] = None

        self._on_track_changed: Optional[Callable[[str, int], None]] = None
        self._on_sink_changed: Optional[Callable[[str, str], None]] = None
        self._on_volume_changed: Optional[Callable[[str, float], None]] = None
        self._on_delay_changed: Optional[Callable[[str, int], None]] = None
        self._on_refresh_sinks: Optional[Callable[[str], None]] = None

        self._build()

    # ------------------------------------------------------------------
    # Callback setters
    # ------------------------------------------------------------------

    def set_track_callback(self, cb: Optional[Callable[[str, int], None]]) -> None:
        self._on_track_changed = cb

    def set_sink_callback(self, cb: Optional[Callable[[str, str], None]]) -> None:
        self._on_sink_changed = cb

    def set_volume_callback(self, cb: Optional[Callable[[str, float], None]]) -> None:
        self._on_volume_changed = cb

    def set_delay_callback(self, cb: Optional[Callable[[str, int], None]]) -> None:
        self._on_delay_changed = cb

    def set_refresh_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        self._on_refresh_sinks = cb

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

    def clear_sinks(self) -> None:
        if self._sink_combo:
            self._sink_combo.remove_all()

    def add_sink(self, name: str, description: str) -> None:
        if self._sink_combo:
            display = description or name
            self._sink_combo.append(name, display)

    def set_selected_sink(self, name: str) -> None:
        if self._sink_combo:
            self._sink_combo.set_active_id(name)

    def get_selected_sink(self) -> str:
        if self._sink_combo:
            active_id = self._sink_combo.get_active_id()
            if active_id is not None:
                return active_id
        return ""

    def set_volume(self, volume: float) -> None:
        if self._volume_scale:
            self._volume_scale.set_value(volume * 100)

    def get_volume(self) -> float:
        if self._volume_scale:
            return self._volume_scale.get_value() / 100.0
        return 1.0

    def set_delay(self, delay_ms: int) -> None:
        if self._delay_spin:
            self._delay_spin.set_value(delay_ms)

    def get_delay(self) -> int:
        if self._delay_spin:
            return int(self._delay_spin.get_value())
        return 0

    def set_controls_sensitive(self, sensitive: bool) -> None:
        for w in (self._track_combo, self._sink_combo, self._volume_scale, self._delay_spin, self._refresh_button):
            if w is not None:
                w.set_sensitive(sensitive)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        grid = Gtk.Grid()
        grid.set_column_spacing(8)
        grid.set_row_spacing(4)
        grid.set_margin_start(8)
        grid.set_margin_end(8)
        grid.set_margin_top(4)
        grid.set_margin_bottom(4)
        self.add(grid)

        row = 0

        # Audio track
        grid.attach(Gtk.Label(label="Audio Track:"), 0, row, 1, 1)
        self._track_combo = Gtk.ComboBoxText()
        self._track_combo.set_hexpand(True)
        self._track_combo.connect("changed", self._on_track_combo_changed)
        grid.attach(self._track_combo, 1, row, 1, 1)
        row += 1

        # Output device
        grid.attach(Gtk.Label(label="Output:"), 0, row, 1, 1)
        self._sink_combo = Gtk.ComboBoxText()
        self._sink_combo.set_hexpand(True)
        self._sink_combo.connect("changed", self._on_sink_combo_changed)
        grid.attach(self._sink_combo, 1, row, 1, 1)

        self._refresh_button = Gtk.Button(label="Refresh")
        self._refresh_button.connect("clicked", self._on_refresh_clicked)
        grid.attach(self._refresh_button, 2, row, 1, 1)
        row += 1

        # Volume
        grid.attach(Gtk.Label(label="Volume:"), 0, row, 1, 1)
        self._volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 200, 1)
        self._volume_scale.set_value(100)
        self._volume_scale.set_hexpand(True)
        self._volume_scale.set_draw_value(True)
        self._volume_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self._volume_scale.connect("value-changed", self._on_volume_changed_internal)
        grid.attach(self._volume_scale, 1, row, 1, 1)
        row += 1

        # Delay
        grid.attach(Gtk.Label(label="Delay:"), 0, row, 1, 1)
        self._delay_spin = Gtk.SpinButton.new_with_range(0, 2000, 1)
        self._delay_spin.set_value(0)
        self._delay_spin.connect("value-changed", self._on_delay_changed_internal)
        delay_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        delay_box.pack_start(self._delay_spin, False, False, 0)
        delay_box.pack_start(Gtk.Label(label="ms"), False, False, 0)
        grid.attach(delay_box, 1, row, 1, 1)

    # ------------------------------------------------------------------
    # Internal signal handlers
    # ------------------------------------------------------------------

    def _on_track_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._on_track_changed and combo.get_active_iter():
            self._on_track_changed(self._listener_id, self.get_selected_track())

    def _on_sink_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._on_sink_changed and combo.get_active_iter():
            self._on_sink_changed(self._listener_id, self.get_selected_sink())

    def _on_refresh_clicked(self, button: Gtk.Button) -> None:
        if self._on_refresh_sinks:
            self._on_refresh_sinks(self._listener_id)

    def _on_volume_changed_internal(self, scale: Gtk.Scale) -> None:
        if self._on_volume_changed:
            self._on_volume_changed(self._listener_id, self.get_volume())

    def _on_delay_changed_internal(self, spin: Gtk.SpinButton) -> None:
        if self._on_delay_changed:
            self._on_delay_changed(self._listener_id, self.get_delay())

    # ------------------------------------------------------------------
    # Block signals helper
    # ------------------------------------------------------------------

    def block_signals(self, blocked: bool = True) -> None:
        pairs = [
            (self._track_combo, self._on_track_combo_changed),
            (self._sink_combo, self._on_sink_combo_changed),
            (self._volume_scale, self._on_volume_changed_internal),
            (self._delay_spin, self._on_delay_changed_internal),
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
