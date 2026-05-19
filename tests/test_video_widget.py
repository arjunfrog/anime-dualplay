"""Tests for ui.video_widget cross-platform behaviour.

Does NOT open a GTK window — tests internal logic only.
"""

from __future__ import annotations

import pytest

import gi
gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gst, Gtk  # noqa: E402

from player.platform import IS_MACOS, IS_LINUX


@pytest.fixture(autouse=True)
def _init_gst() -> None:
    Gst.init(None)


class TestVideoWidgetPlatform:
    def test_widget_type_differs_by_platform(self) -> None:
        from ui.video_widget import VideoWidget
        w = VideoWidget()
        if IS_MACOS:
            assert isinstance(w, Gtk.Box)
        else:
            assert isinstance(w, Gtk.DrawingArea)

    def test_is_realized_for_video_true_on_macos(self) -> None:
        if not IS_MACOS:
            pytest.skip("macOS only")
        from ui.video_widget import VideoWidget
        w = VideoWidget()
        assert w.is_realized_for_video is True

    def test_is_realized_for_video_false_before_realize_on_linux(self) -> None:
        if not IS_LINUX:
            pytest.skip("Linux only")
        from ui.video_widget import VideoWidget
        w = VideoWidget()
        assert w.is_realized_for_video is False

    def test_create_video_sink_returns_element(self) -> None:
        from ui.video_widget import VideoWidget
        w = VideoWidget()
        sink = w.create_video_sink()
        if IS_MACOS:
            assert sink is not None, "Should create gtksink or fallback on macOS"
        else:
            pass
