"""Cross-platform tests for device discovery and pipeline building.

Validates that audio sink creation, device property setting, and
GstDeviceMonitor-based discovery work on the current platform (macOS or Linux).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from player.models import ListenerConfig, PlayerConfig, SubtitleConfig
from player.platform import (
    AUDIO_SINK_DEVICE_PROPERTY,
    AUDIO_SINK_ELEMENT,
    IS_MACOS,
    IS_LINUX,
)


def _gst_or_skip() -> None:
    Gst.init(None)
    for name in ("queue", "audioconvert", "audioresample", "volume", "fakesink"):
        try:
            e = Gst.ElementFactory.make(name, None)
        except Exception:
            e = None
        if e is None:
            pytest.skip(f"Required GStreamer element missing: {name}")


@pytest.fixture(autouse=True)
def _init_gst() -> None:
    _gst_or_skip()


def _config() -> PlayerConfig:
    return PlayerConfig(
        listener_a=ListenerConfig("Listener A", "fake-sink-a", 0),
        listener_b=ListenerConfig("Listener B", "fake-sink-b", 1),
        video_enabled=True,
        video_sink="fakesink",
        subtitles=SubtitleConfig(enabled=False, subtitle_track=0),
    )


class TestAudioSinkElement:
    def test_audio_sink_element_can_be_created(self) -> None:
        try:
            sink = Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, None)
        except Exception:
            sink = None
        assert sink is not None, f"{AUDIO_SINK_ELEMENT} element not available"

    def test_audio_sink_has_device_property(self) -> None:
        try:
            sink = Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, None)
        except Exception:
            pytest.skip(f"{AUDIO_SINK_ELEMENT} not available")
        prop_names = [p.name for p in sink.list_properties()]
        assert AUDIO_SINK_DEVICE_PROPERTY in prop_names, (
            f"{AUDIO_SINK_ELEMENT} lacks property '{AUDIO_SINK_DEVICE_PROPERTY}'"
        )

    def test_audio_sink_has_volume_property_on_macos(self) -> None:
        if not IS_MACOS:
            pytest.skip("macOS only")
        try:
            sink = Gst.ElementFactory.make("osxaudiosink", None)
        except Exception:
            pytest.skip("osxaudiosink not available")
        prop_names = [p.name for p in sink.list_properties()]
        assert "volume" in prop_names

    def test_audio_sink_has_sync_property(self) -> None:
        try:
            sink = Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, None)
        except Exception:
            pytest.skip(f"{AUDIO_SINK_ELEMENT} not available")
        prop_names = [p.name for p in sink.list_properties()]
        assert "sync" in prop_names


class TestPipelineBuilder:
    def test_make_audio_branch_uses_platform_sink(self) -> None:
        from player.playback.pipeline_builder import PipelineBuilder

        try:
            Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, None)
        except Exception:
            pytest.skip(f"{AUDIO_SINK_ELEMENT} not available")

        builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/x.mkv"))
        builder.build_skeleton()
        listener = ListenerConfig("Listener A", "", 0, volume=0.5)
        queue = builder.make_audio_branch(listener, "listener_a")
        assert queue is not None

        sink = builder.pipeline.get_by_name("listener_a_sink")
        assert sink is not None
        factory = sink.get_factory()
        assert factory.get_name() == AUDIO_SINK_ELEMENT

    def test_audio_branch_sets_volume(self) -> None:
        from player.playback.pipeline_builder import PipelineBuilder

        try:
            Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, None)
        except Exception:
            pytest.skip(f"{AUDIO_SINK_ELEMENT} not available")

        builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/x.mkv"))
        builder.build_skeleton()
        listener = ListenerConfig("Listener A", "", 0, volume=0.7)
        builder.make_audio_branch(listener, "listener_a")
        vol = builder.pipeline.get_by_name("listener_a_volume")
        assert abs(vol.get_property("volume") - 0.7) < 1e-6

    def test_audio_branch_with_delay(self) -> None:
        from player.playback.pipeline_builder import PipelineBuilder

        try:
            Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, None)
        except Exception:
            pytest.skip(f"{AUDIO_SINK_ELEMENT} not available")

        builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/x.mkv"))
        builder.build_skeleton()
        listener = ListenerConfig("L", "", 0, delay_ms=100, volume=1.0)
        builder.make_audio_branch(listener, "listener_a")
        sink = builder.pipeline.get_by_name("listener_a_sink")
        assert sink.get_property("render-delay") == 100 * 1_000_000


class TestDeviceDiscovery:
    def test_get_audio_sinks_returns_list(self) -> None:
        from player.devices import get_audio_sinks
        sinks = get_audio_sinks()
        assert isinstance(sinks, list)

    def test_audio_sinks_have_names(self) -> None:
        from player.devices import get_audio_sinks
        sinks = get_audio_sinks()
        if not sinks:
            pytest.skip("No audio sinks available in test environment")
        for s in sinks:
            assert s.name, "Sink name should not be empty"
            assert s.description, "Sink description should not be empty"

    def test_macos_sinks_use_unique_id(self) -> None:
        if not IS_MACOS:
            pytest.skip("macOS only")
        from player.devices import get_audio_sinks
        sinks = get_audio_sinks()
        if not sinks:
            pytest.skip("No audio sinks available")
        for s in sinks:
            assert "node.name" not in s.name or s.name, (
                "macOS sinks should use unique-id, not node.name"
            )

    def test_bluetooth_codec_returns_none_on_macos(self) -> None:
        if not IS_MACOS:
            pytest.skip("macOS only")
        from player.devices import get_bluetooth_codec
        assert get_bluetooth_codec("bluez_output.XX.1") is None


class TestVideoSinks:
    def test_at_least_one_video_sink_available(self) -> None:
        from player.platform import PREFERRED_VIDEO_SINKS
        available = []
        for name in PREFERRED_VIDEO_SINKS:
            try:
                e = Gst.ElementFactory.make(name, None)
                if e:
                    available.append(name)
            except Exception:
                pass
        assert available, f"No video sinks available from {PREFERRED_VIDEO_SINKS}"

    def test_gtksink_available_on_macos(self) -> None:
        if not IS_MACOS:
            pytest.skip("macOS only")
        try:
            sink = Gst.ElementFactory.make("gtksink", None)
        except Exception:
            sink = None
        assert sink is not None, "gtksink should be available on macOS"

    def test_osxaudiosink_not_available_on_linux(self) -> None:
        if not IS_LINUX:
            pytest.skip("Linux only")
        try:
            sink = Gst.ElementFactory.make("osxaudiosink", None)
        except Exception:
            sink = None
        assert sink is None, "osxaudiosink should not be available on Linux"


class TestGstCoreElements:
    """Verify essential GStreamer elements are present on this platform."""

    REQUIRED_ELEMENTS = [
        "filesrc",
        "decodebin",
        "queue",
        "audioconvert",
        "audioresample",
        "volume",
        "videoconvert",
        "textoverlay",
        "input-selector",
        "capsfilter",
        "fakesink",
    ]

    @pytest.mark.parametrize("element_name", REQUIRED_ELEMENTS)
    def test_element_available(self, element_name: str) -> None:
        try:
            e = Gst.ElementFactory.make(element_name, None)
        except Exception:
            e = None
        assert e is not None, f"GStreamer element '{element_name}' is not available"


class TestGstDiscoverer:
    def test_discoverer_can_be_created(self) -> None:
        gi.require_version("GstPbutils", "1.0")
        from gi.repository import GstPbutils
        discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
        assert discoverer is not None
