"""Tests covering PlaybackSession._rebuild_pipeline failure paths.

The contract: when the GUI video_sink_factory raises or returns None,
_rebuild_pipeline must NOT destroy the existing pipeline and must surface
the failure via the status + error callbacks. This prevents the session
from silently transitioning into a dead state with no UI feedback.
"""

from __future__ import annotations

from pathlib import Path

from player.models import ListenerConfig, PlayerConfig, SubtitleConfig
from player.playback.session import PlaybackSession


def _config() -> PlayerConfig:
    return PlayerConfig(
        listener_a=ListenerConfig("Listener A", "sink-a", 0),
        listener_b=ListenerConfig("Listener B", "sink-b", 1),
        subtitles=SubtitleConfig(enabled=False, subtitle_track=0),
    )


def _make_gui_session() -> PlaybackSession:
    session = PlaybackSession(_config(), Path("movie.mkv"), headless=False)
    # Sentinel pipeline so we can detect whether destroy_pipeline ran.
    sentinel = object()
    session.pipeline = sentinel  # type: ignore[assignment]
    # Patch destroy_pipeline / get_position so the rebuild can run without
    # GStreamer being involved.
    destroyed = {"called": False}

    def _no_destroy() -> None:
        destroyed["called"] = True

    session.destroy_pipeline = _no_destroy  # type: ignore[assignment]
    session.get_position = lambda: 0.0  # type: ignore[assignment]
    session._destroyed_flag = destroyed  # type: ignore[attr-defined]
    session._sentinel = sentinel  # type: ignore[attr-defined]
    return session


def test_rebuild_aborts_when_factory_raises() -> None:
    session = _make_gui_session()

    errors: list[str] = []
    statuses: list[tuple[str, bool]] = []
    session.set_error_callback(errors.append)
    session.set_status_callback(lambda msg, is_err: statuses.append((msg, is_err)))

    def _bad_factory():
        raise RuntimeError("no display")

    session.set_video_sink_factory(_bad_factory)

    session._rebuild_pipeline()

    assert session._destroyed_flag["called"] is False  # type: ignore[attr-defined]
    assert session.pipeline is session._sentinel  # type: ignore[attr-defined]
    assert errors, "expected on_error to be called"
    assert any(is_err for _, is_err in statuses), "expected an error status"
    assert "no display" in errors[0]


def test_rebuild_aborts_when_factory_returns_none() -> None:
    session = _make_gui_session()

    errors: list[str] = []
    session.set_error_callback(errors.append)
    session.set_video_sink_factory(lambda: None)

    session._rebuild_pipeline()

    assert session._destroyed_flag["called"] is False  # type: ignore[attr-defined]
    assert session.pipeline is session._sentinel  # type: ignore[attr-defined]
    assert errors, "expected on_error to be called"


def test_rebuild_aborts_when_no_factory_set_in_gui_mode() -> None:
    session = _make_gui_session()

    errors: list[str] = []
    session.set_error_callback(errors.append)
    # No factory set — GUI rebuild has no way to obtain a fresh sink.

    session._rebuild_pipeline()

    assert session._destroyed_flag["called"] is False  # type: ignore[attr-defined]
    assert session.pipeline is session._sentinel  # type: ignore[attr-defined]
    assert errors, "expected on_error to be called"
