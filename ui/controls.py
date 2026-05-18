from __future__ import annotations

from typing import Optional, Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib


class PlaybackControls(Gtk.Box):
    """Playback control bar: prev, play/pause, stop, next, seek bar, time display."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        self._playing = False
        self._seek_dragging = False
        self._duration = 0.0

        self._on_play: Optional[Callable[[], None]] = None
        self._on_pause: Optional[Callable[[], None]] = None
        self._on_stop: Optional[Callable[[], None]] = None
        self._on_seek: Optional[Callable[[float], None]] = None
        self._on_previous: Optional[Callable[[], None]] = None
        self._on_next: Optional[Callable[[], None]] = None
        self._on_toggle_sidebar: Optional[Callable[[], None]] = None

        self._prev_button: Optional[Gtk.Button] = None
        self._play_pause_button: Optional[Gtk.Button] = None
        self._stop_button: Optional[Gtk.Button] = None
        self._next_button: Optional[Gtk.Button] = None
        self._sidebar_toggle_button: Optional[Gtk.Button] = None
        self._seek_bar: Optional[Gtk.Scale] = None
        self._seek_value_changed_handler: int = 0
        self._time_label: Optional[Gtk.Label] = None

        self._build()

    # ------------------------------------------------------------------
    # Callback setters
    # ------------------------------------------------------------------

    def set_play_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_play = cb

    def set_pause_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_pause = cb

    def set_stop_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_stop = cb

    def set_seek_callback(self, cb: Optional[Callable[[float], None]]) -> None:
        self._on_seek = cb

    def set_previous_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_previous = cb

    def set_next_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_next = cb

    def set_sidebar_toggle_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_toggle_sidebar = cb

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if self._play_pause_button:
            self._play_pause_button.set_label("⏸" if playing else "▶")

    def set_position(self, position: float, duration: float) -> None:
        self._duration = max(duration, 0.001)
        if not self._seek_dragging:
            self._seek_bar.handler_block(self._seek_value_changed_handler)
            self._seek_bar.set_range(0, self._duration)
            self._seek_bar.set_value(min(position, self._duration))
            self._seek_bar.handler_unblock(self._seek_value_changed_handler)
        self._time_label.set_text(f"{_format_time(position)} / {_format_time(duration)}")

    def set_controls_sensitive(self, sensitive: bool) -> None:
        for w in (self._prev_button, self._play_pause_button, self._stop_button, self._next_button, self._sidebar_toggle_button, self._seek_bar):
            if w is not None:
                w.set_sensitive(sensitive)

    def set_previous_sensitive(self, sensitive: bool) -> None:
        if self._prev_button is not None:
            self._prev_button.set_sensitive(sensitive)

    def set_next_sensitive(self, sensitive: bool) -> None:
        if self._next_button is not None:
            self._next_button.set_sensitive(sensitive)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # Previous button
        self._prev_button = Gtk.Button(label="⏮")
        self._prev_button.set_size_request(48, 32)
        self._prev_button.set_tooltip_text("Previous track")
        self._prev_button.connect("clicked", self._on_prev_clicked)
        self.pack_start(self._prev_button, False, False, 0)

        # Play/Pause button
        self._play_pause_button = Gtk.Button(label="▶")
        self._play_pause_button.set_size_request(48, 32)
        self._play_pause_button.connect("clicked", self._on_play_pause_clicked)
        self.pack_start(self._play_pause_button, False, False, 0)

        # Stop button
        self._stop_button = Gtk.Button(label="⏹")
        self._stop_button.set_size_request(48, 32)
        self._stop_button.connect("clicked", self._on_stop_clicked)
        self.pack_start(self._stop_button, False, False, 0)

        # Next button
        self._next_button = Gtk.Button(label="⏭")
        self._next_button.set_size_request(48, 32)
        self._next_button.set_tooltip_text("Next track")
        self._next_button.connect("clicked", self._on_next_clicked)
        self.pack_start(self._next_button, False, False, 0)

        # Sidebar toggle button
        self._sidebar_toggle_button = Gtk.Button(label="◫")
        self._sidebar_toggle_button.set_size_request(40, 32)
        self._sidebar_toggle_button.set_tooltip_text("Toggle Playlist Sidebar")
        self._sidebar_toggle_button.connect("clicked", self._on_sidebar_toggle_clicked)
        self.pack_end(self._sidebar_toggle_button, False, False, 0)

        # Seek bar
        self._seek_bar = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self._seek_bar.set_hexpand(True)
        self._seek_bar.set_draw_value(False)
        self._seek_bar.set_value(0)
        self._seek_value_changed_handler = self._seek_bar.connect(
            "change-value", self._on_seek_change_value
        )
        self._seek_bar.connect("button-press-event", self._on_seek_button_press)
        self._seek_bar.connect("button-release-event", self._on_seek_button_release)
        self.pack_start(self._seek_bar, True, True, 0)

        # Time label
        self._time_label = Gtk.Label(label="00:00 / 00:00")
        self.pack_start(self._time_label, False, False, 0)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_play_pause_clicked(self, button: Gtk.Button) -> None:
        if self._playing:
            if self._on_pause:
                self._on_pause()
        else:
            if self._on_play:
                self._on_play()

    def _on_prev_clicked(self, button: Gtk.Button) -> None:
        if self._on_previous:
            self._on_previous()

    def _on_next_clicked(self, button: Gtk.Button) -> None:
        if self._on_next:
            self._on_next()

    def _on_sidebar_toggle_clicked(self, button: Gtk.Button) -> None:
        if self._on_toggle_sidebar:
            self._on_toggle_sidebar()

    def _on_stop_clicked(self, button: Gtk.Button) -> None:
        if self._on_stop:
            self._on_stop()

    def _on_seek_button_press(self, widget, event) -> None:
        self._seek_dragging = True

    def _on_seek_button_release(self, widget, event) -> None:
        self._seek_dragging = False
        if self._on_seek:
            self._on_seek(self._seek_bar.get_value())

    def _on_seek_change_value(self, scale, scroll, value) -> bool:
        return False  # allow the change


def _format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
