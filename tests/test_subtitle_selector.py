"""Subtitle in-place switching tests.

After the engine refactor, activate_subtitle_track lives on PadRouter and
PlaybackSession.set_subtitle_track delegates to it. These tests exercise
both layers with fake selector elements so no real GStreamer pipeline is
needed.
"""

from pathlib import Path

from player.engine import PlaybackEngine
from player.models import ListenerConfig, MediaInfo, PlayerConfig, SubtitleConfig
from player.playback.pad_router import PadRouter
from player.playback.pipeline_builder import PipelineBuilder


class FakeSelector:
    def __init__(self) -> None:
        self.properties: dict[str, object] = {}

    def set_property(self, name: str, value: object) -> None:
        self.properties[name] = value


class FakeAsyncSelector(FakeSelector):
    def __init__(self) -> None:
        super().__init__()
        self.async_calls = 0

    def call_async(self, func, user_data=None) -> None:
        self.async_calls += 1
        func(self, user_data)


def _config() -> PlayerConfig:
    return PlayerConfig(
        listener_a=ListenerConfig("Listener A", "sink-a", 0),
        listener_b=ListenerConfig("Listener B", "sink-b", 1),
        subtitles=SubtitleConfig(enabled=True, subtitle_track=0),
    )


def _builder_with_selector(selector: object) -> PipelineBuilder:
    """Create a PipelineBuilder skeleton without a real pipeline and
    inject the fake selector. Tests use this to exercise the routing
    layer without GStreamer."""
    builder = PipelineBuilder(
        config=_config(),
        media_path=Path("movie.mkv"),
    )
    builder.subtitle_selector = selector  # type: ignore[assignment]
    return builder


def _router(builder: PipelineBuilder) -> PadRouter:
    return PadRouter(
        builder=builder,
        config=_config(),
        media_info=MediaInfo(uri="file:///movie.mkv", duration=0),
    )


# --- PadRouter.activate_subtitle_track ---------------------------------

def test_activate_subtitle_track_sets_selector_active_pad() -> None:
    selector = FakeSelector()
    pad = object()
    builder = _builder_with_selector(selector)
    builder.subtitle_selector_sink_pads[1] = pad  # type: ignore[assignment]
    router = _router(builder)

    assert router.activate_subtitle_track(1)
    assert selector.properties["active-pad"] is pad
    assert router.subtitle_connected
    assert router.pending_subtitle_track is None


def test_activate_subtitle_track_defers_until_pad_arrives() -> None:
    builder = _builder_with_selector(FakeSelector())
    router = _router(builder)

    assert not router.activate_subtitle_track(3)
    assert router.pending_subtitle_track == 3


# --- PlaybackSession.set_subtitle_track no-rebuild path ----------------

class NoRebuildEngine(PlaybackEngine):
    def __init__(self) -> None:
        super().__init__(_config(), Path("movie.mkv"))
        self.rebuilt = False

    def _rebuild_pipeline(self) -> None:
        self.rebuilt = True

    def seek(self, seconds: float, accurate: bool = True) -> bool:
        raise AssertionError("subtitle track switching must not seek")


def _inject(engine: NoRebuildEngine, selector: object, track_index: int, pad: object) -> None:
    builder = PipelineBuilder(
        config=engine.config,
        media_path=engine.media_path,
    )
    builder.subtitle_selector = selector  # type: ignore[assignment]
    builder.subtitle_selector_sink_pads[track_index] = pad  # type: ignore[assignment]
    engine._builder = builder  # type: ignore[attr-defined]
    engine._router = PadRouter(
        builder=builder,
        config=engine.config,
        media_info=engine.media_info,
        status_emitter=engine._emit_status,  # type: ignore[attr-defined]
    )


def test_set_subtitle_track_switches_in_place_when_selector_pad_exists() -> None:
    selector = FakeSelector()
    pad = object()
    engine = NoRebuildEngine()
    _inject(engine, selector, 2, pad)

    engine.set_subtitle_track(2)

    assert not engine.rebuilt
    assert selector.properties["active-pad"] is pad
    assert engine.config.subtitles.subtitle_track == 2


def test_set_subtitle_track_uses_async_selector_switch_when_available() -> None:
    selector = FakeAsyncSelector()
    pad = object()
    engine = NoRebuildEngine()
    _inject(engine, selector, 2, pad)

    engine.set_subtitle_track(2)

    assert not engine.rebuilt
    assert selector.async_calls == 1
    assert selector.properties["active-pad"] is pad
    assert engine.config.subtitles.subtitle_track == 2
