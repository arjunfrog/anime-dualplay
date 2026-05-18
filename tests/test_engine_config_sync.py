from pathlib import Path

from player.engine import PlaybackEngine
from player.models import PlayerConfig, ListenerConfig, SubtitleConfig


def _config() -> PlayerConfig:
    return PlayerConfig(
        listener_a=ListenerConfig("Listener A", "sink-a", 0),
        listener_b=ListenerConfig("Listener B", "sink-b", 1),
        subtitles=SubtitleConfig(enabled=True, subtitle_track=0),
    )


def test_set_video_delay_syncs_config():
    engine = PlaybackEngine(_config(), Path("movie.mkv"))
    engine.set_video_delay(150)
    assert engine.config.video_delay_ms == 150


def test_set_video_delay_clamps_and_syncs():
    engine = PlaybackEngine(_config(), Path("movie.mkv"))
    engine.set_video_delay(5000)
    assert engine.config.video_delay_ms == 2000


def test_set_listener_delay_syncs_config_a():
    engine = PlaybackEngine(_config(), Path("movie.mkv"))
    engine.set_listener_delay("listener_a", 200)
    assert engine.config.listener_a.delay_ms == 200


def test_set_listener_delay_syncs_config_b():
    engine = PlaybackEngine(_config(), Path("movie.mkv"))
    engine.set_listener_delay("listener_b", 300)
    assert engine.config.listener_b.delay_ms == 300


def test_set_listener_delay_clamps_and_syncs():
    engine = PlaybackEngine(_config(), Path("movie.mkv"))
    engine.set_listener_delay("listener_a", -50)
    assert engine.config.listener_a.delay_ms == 0
