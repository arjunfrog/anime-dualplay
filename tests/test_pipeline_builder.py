"""Tests for player.playback.pipeline_builder.

Uses real GStreamer element factories where the elements are well-known
core/base/good plugins. Skips automatically if a plugin is missing in
the test environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from player.models import (
    ListenerConfig,
    PlayerConfig,
    SubtitleConfig,
    SubtitleStyle,
)
from player.playback.pipeline_builder import PipelineBuilder


def _gst_or_skip() -> None:
    Gst.init(None)
    for name in ("queue", "filesrc", "decodebin", "videoconvert", "fakesink"):
        if Gst.ElementFactory.make(name, None) is None:
            pytest.skip(f"Required GStreamer element missing: {name}")


@pytest.fixture(autouse=True)
def _init_gst() -> None:
    _gst_or_skip()


def _config(video: bool = True, subs: bool = False) -> PlayerConfig:
    return PlayerConfig(
        listener_a=ListenerConfig("Listener A", "fake-sink-a", 0),
        listener_b=ListenerConfig("Listener B", "fake-sink-b", 1),
        video_enabled=video,
        video_sink="fakesink",
        subtitles=SubtitleConfig(enabled=subs, subtitle_track=0),
    )


# --- Skeleton ----------------------------------------------------------

def test_build_skeleton_creates_pipeline_with_filesrc_and_decodebin() -> None:
    builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/movie.mkv"))
    pipeline, decodebin = builder.build_skeleton()
    assert pipeline is not None
    assert decodebin is not None
    assert pipeline.get_by_name("source") is not None
    assert pipeline.get_by_name("decodebin") is decodebin


def test_skeleton_filesrc_uses_resolved_media_path(tmp_path: Path) -> None:
    target = tmp_path / "x.mkv"
    target.touch()
    builder = PipelineBuilder(config=_config(), media_path=target)
    pipeline, _ = builder.build_skeleton()
    src = pipeline.get_by_name("source")
    assert src is not None
    assert src.get_property("location") == str(target.resolve())


# --- Audio branch ------------------------------------------------------

def test_make_audio_branch_returns_queue_input() -> None:
    builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/x.mkv"))
    builder.build_skeleton()
    listener = ListenerConfig("Listener A", "fake-sink", 0, volume=0.5)
    queue = builder.make_audio_branch(listener, "listener_a")
    assert queue.get_name() == "listener_a_queue"
    assert builder.pipeline.get_by_name("listener_a_volume") is not None
    assert builder.pipeline.get_by_name("listener_a_sink") is not None


def test_audio_branch_applies_volume_and_delay() -> None:
    builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/x.mkv"))
    builder.build_skeleton()
    listener = ListenerConfig("L", "fake-sink", 0, delay_ms=120, volume=0.8)
    builder.make_audio_branch(listener, "listener_a")
    vol = builder.pipeline.get_by_name("listener_a_volume")
    sink = builder.pipeline.get_by_name("listener_a_sink")
    assert abs(vol.get_property("volume") - 0.8) < 1e-6
    assert sink.get_property("render-delay") == 120 * 1_000_000


def test_audio_branch_negative_delay_emits_warning_and_does_not_set_render_delay() -> None:
    captured: list[tuple[str, bool]] = []

    builder = PipelineBuilder(
        config=_config(),
        media_path=Path("/tmp/x.mkv"),
        status_emitter=lambda msg, err: captured.append((msg, err)),
    )
    builder.build_skeleton()
    listener = ListenerConfig("L", "fake-sink", 0, delay_ms=-50, volume=1.0)
    builder.make_audio_branch(listener, "listener_b")

    assert any("negative delay" in m for m, _ in captured)
    sink = builder.pipeline.get_by_name("listener_b_sink")
    # render-delay was never set, so it's 0.
    assert sink.get_property("render-delay") == 0


# --- Video branch ------------------------------------------------------

def test_ensure_video_branch_returns_none_when_video_disabled() -> None:
    builder = PipelineBuilder(config=_config(video=False), media_path=Path("/tmp/x.mkv"))
    builder.build_skeleton()
    assert builder.ensure_video_branch() is None
    assert builder.video_queue is None


def test_ensure_video_branch_creates_queue_and_delay_queue() -> None:
    builder = PipelineBuilder(config=_config(video=True), media_path=Path("/tmp/x.mkv"))
    builder.build_skeleton()
    queue = builder.ensure_video_branch()
    assert queue is not None
    assert builder.video_queue is queue
    assert builder.video_delay_queue is not None
    assert builder.video_sink_internal is not None


def test_ensure_video_branch_is_idempotent() -> None:
    builder = PipelineBuilder(config=_config(video=True), media_path=Path("/tmp/x.mkv"))
    builder.build_skeleton()
    first = builder.ensure_video_branch()
    second = builder.ensure_video_branch()
    assert first is second


def test_video_branch_with_subtitles_creates_overlay_and_selector() -> None:
    builder = PipelineBuilder(
        config=_config(video=True, subs=True),
        media_path=Path("/tmp/x.mkv"),
    )
    builder.build_skeleton()
    builder.ensure_video_branch()
    assert builder.subtitle_overlay is not None
    assert builder.subtitle_selector is not None
    assert builder.subtitle_capsfilter is not None


# --- Subtitle branch ---------------------------------------------------

def test_make_subtitle_branch_records_selector_pad_in_map() -> None:
    builder = PipelineBuilder(
        config=_config(video=True, subs=True),
        media_path=Path("/tmp/x.mkv"),
    )
    builder.build_skeleton()
    builder.ensure_video_branch()
    queue = builder.make_subtitle_branch(track_index=2)
    assert queue is not None
    assert 2 in builder.subtitle_selector_sink_pads


def test_make_subtitle_branch_each_call_records_distinct_pad() -> None:
    builder = PipelineBuilder(
        config=_config(video=True, subs=True),
        media_path=Path("/tmp/x.mkv"),
    )
    builder.build_skeleton()
    builder.ensure_video_branch()
    builder.make_subtitle_branch(track_index=0)
    builder.make_subtitle_branch(track_index=1)
    pads = builder.subtitle_selector_sink_pads
    assert pads[0] is not pads[1]


# --- Discard branch ----------------------------------------------------

def test_make_discard_branch_creates_queue_and_fakesink() -> None:
    builder = PipelineBuilder(config=_config(), media_path=Path("/tmp/x.mkv"))
    builder.build_skeleton()
    queue = builder.make_discard_branch("audio_3")
    assert queue.get_name() == "discard_audio_3_queue"
    assert builder.pipeline.get_by_name("discard_audio_3_sink") is not None


# --- Subtitle style ----------------------------------------------------

def test_apply_subtitle_style_updates_overlay_in_place() -> None:
    style1 = SubtitleStyle(font_desc="Sans 12", halignment=0)
    builder = PipelineBuilder(
        config=_config(video=True, subs=True),
        media_path=Path("/tmp/x.mkv"),
        subtitle_style=style1,
    )
    builder.build_skeleton()
    builder.ensure_video_branch()
    overlay = builder.subtitle_overlay
    assert overlay.get_property("font-desc") == "Sans 12"
    assert overlay.get_property("halignment") == 0

    style2 = SubtitleStyle(font_desc="Mono 24", halignment=2)
    builder.apply_subtitle_style(style2)
    assert overlay.get_property("font-desc") == "Mono 24"
    assert overlay.get_property("halignment") == 2
