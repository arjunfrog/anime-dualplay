from __future__ import annotations

import logging
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gtk, Gdk, Gst, GstVideo  # noqa: E402

from player.platform import IS_MACOS, PREFERRED_VIDEO_SINKS

_logger = logging.getLogger("dual_audio_player")

if not IS_MACOS:
    try:
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11  # noqa: E402, F401
        _HAS_X11 = True
    except (ValueError, ImportError):
        _HAS_X11 = False
else:
    _HAS_X11 = False


class VideoWidget(Gtk.DrawingArea if not IS_MACOS else Gtk.Box):
    """A GTK widget that embeds GStreamer video.

    On Linux/X11 this is a DrawingArea that receives video via XID overlay.
    On macOS this is a Box that wraps a gtksink-provided widget, since
    macOS has no X11 window handles.
    """

    def __init__(self):
        super().__init__()
        if IS_MACOS:
            self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_size_request(640, 360)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)

        self._xid: int = 0
        self._video_sink: Optional[Gst.Element] = None
        self._gtksink_widget: Optional[Gtk.Widget] = None

        if not IS_MACOS:
            self.set_double_buffered(True)
            self.connect("realize", self._on_realize)
            self.connect("size-allocate", self._on_size_allocate)
            self.connect("unrealize", self._on_unrealize)

        self._realized = False
        if IS_MACOS:
            self._realized = True

    @property
    def is_realized_for_video(self) -> bool:
        if IS_MACOS:
            return True
        return self._realized and self._xid != 0

    def create_video_sink(self) -> Optional[Gst.Element]:
        """Create a fresh GStreamer video sink, bind it to this widget's
        window, and return it. Each call returns a new element; the most
        recent one is tracked here so size-allocate can exposed() it."""
        if self._gtksink_widget is not None:
            self.remove(self._gtksink_widget)
            self._gtksink_widget = None

        if IS_MACOS:
            sink = Gst.ElementFactory.make("gtksink", None)
            if sink:
                try:
                    self._gtksink_widget = sink.get_property("widget")
                    if self._gtksink_widget:
                        self._gtksink_widget.set_hexpand(True)
                        self._gtksink_widget.set_vexpand(True)
                        self.pack_start(self._gtksink_widget, True, True, 0)
                        self._gtksink_widget.show()
                        self._video_sink = sink
                        return sink
                except Exception as exc:
                    _logger.warning("gtksink widget extraction failed: %s", exc)

            for name in PREFERRED_VIDEO_SINKS[1:]:
                sink = Gst.ElementFactory.make(name, None)
                if sink:
                    self._video_sink = sink
                    return sink
            return None

        sink = Gst.ElementFactory.make("xvimagesink", None)
        if not sink:
            sink = Gst.ElementFactory.make("ximagesink", None)
        if not sink:
            sink = Gst.ElementFactory.make("autovideosink", None)
        if not sink:
            return None

        try:
            sink.set_property("force-aspect-ratio", True)
        except Exception:
            pass

        if self._xid != 0:
            try:
                sink.set_window_handle(self._xid)
            except Exception as exc:
                _logger.warning("Could not set video overlay window handle: %s", exc)

        self._video_sink = sink
        return sink

    def _on_realize(self, widget: Gtk.DrawingArea) -> None:
        window = widget.get_window()
        if window is None:
            return
        try:
            self._xid = window.get_xid()
        except Exception as exc:
            _logger.warning("Could not get X11 window ID: %s", exc)
            return
        self._realized = True

        if self._video_sink is not None:
            try:
                self._video_sink.set_window_handle(self._xid)
            except Exception as exc:
                _logger.warning("Could not set video overlay window handle (late): %s", exc)

        widget.override_background_color(
            Gtk.StateFlags.NORMAL,
            Gdk.RGBA(0.0, 0.0, 0.0, 1.0),
        )

    def _on_size_allocate(self, widget: Gtk.DrawingArea, allocation: Gdk.Rectangle) -> None:
        if self._video_sink is not None:
            try:
                self._video_sink.expose()
            except Exception:
                pass

    def _on_unrealize(self, widget: Gtk.DrawingArea) -> None:
        self._xid = 0
        self._realized = False
        self._video_sink = None
