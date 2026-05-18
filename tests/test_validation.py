from pathlib import Path

from player.models import ListenerConfig, MediaInfo, PlayerConfig, SinkInfo, SubtitleConfig, TrackInfo
from player.validation import (
    clamp_delay_ms, clamp_video_delay_ms, clamp_volume,
    is_bluetooth_sink, normalize_config, suggest_delay_for_sink,
    validate_playback_config,
)


def _media() -> MediaInfo:
    return MediaInfo(
        uri="file:///tmp/movie.mkv",
        duration=10.0,
        audio_tracks=[
            TrackInfo(index=0, global_index=1, type="audio", codec="AAC", language="ja", stream_id="a0"),
            TrackInfo(index=1, global_index=2, type="audio", codec="AAC", language="en", stream_id="a1"),
        ],
        subtitle_tracks=[
            TrackInfo(index=0, global_index=3, type="subtitle", codec="SRT", language="en", stream_id="s0"),
        ],
    )


def _config() -> PlayerConfig:
    return PlayerConfig(
        listener_a=ListenerConfig("Listener A", "sink-a", 0),
        listener_b=ListenerConfig("Listener B", "sink-b", 1),
        subtitles=SubtitleConfig(enabled=True, subtitle_track=0),
    )


def test_clamps_volume_and_delay() -> None:
    config = _config()
    config.listener_a.volume = 4.0
    config.listener_b.volume = -1.0
    config.listener_a.delay_ms = 5000
    config.listener_b.delay_ms = -200

    normalize_config(config)

    assert config.listener_a.volume == 2.0
    assert config.listener_b.volume == 0.0
    assert config.listener_a.delay_ms == 2000
    assert config.listener_b.delay_ms == 0
    assert clamp_volume(1.25) == 1.25
    assert clamp_delay_ms(42) == 42


def test_validation_accepts_duplicate_audio_track_with_warning(tmp_path: Path) -> None:
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"placeholder")
    config = _config()
    config.listener_b.audio_track = 0

    result = validate_playback_config(
        config,
        media_path,
        _media(),
        [SinkInfo("sink-a"), SinkInfo("sink-b")],
    )

    assert result.ok
    assert "fan out" in result.warnings[0]


def test_validation_rejects_missing_sink_and_bad_tracks(tmp_path: Path) -> None:
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"placeholder")
    config = _config()
    config.listener_a.sink = ""
    config.listener_b.audio_track = 9
    config.subtitles.subtitle_track = 5

    result = validate_playback_config(
        config,
        media_path,
        _media(),
        [SinkInfo("sink-b")],
    )

    assert not result.ok
    assert any("Listener A has no output sink" in error for error in result.errors)
    assert any("Listener B audio track 9" in error for error in result.errors)
    assert any("Subtitle track 5" in error for error in result.errors)


def test_validation_rejects_empty_discovered_sink_list(tmp_path: Path) -> None:
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"placeholder")

    result = validate_playback_config(_config(), media_path, _media(), [])

    assert not result.ok
    assert "No audio output sinks were discovered." in result.errors


class TestClampOnError:
    def test_missing_sinks_become_warnings_not_errors(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()

        result = validate_playback_config(config, media_path, _media(), [], clamp_on_error=True)

        assert result.ok
        assert any("No audio output sinks discovered" in w for w in result.warnings)
        assert not any("No audio output sinks were discovered" in e for e in result.errors)

    def test_unavailable_audio_track_clamped_with_warning(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.listener_b.audio_track = 9

        result = validate_playback_config(
            config, media_path, _media(),
            [SinkInfo("sink-a"), SinkInfo("sink-b")],
            clamp_on_error=True,
        )

        assert result.ok
        assert "listener_b" in result.auto_clamped
        assert result.auto_clamped["listener_b"] == {"audio_track": 0}
        assert any("audio track 9" in w for w in result.warnings)

    def test_no_audio_track_error_with_zero_audio_tracks(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.listener_b.audio_track = 9
        media = _media()
        media.audio_tracks = []

        result = validate_playback_config(
            config, media_path, media,
            [SinkInfo("sink-a")],
            clamp_on_error=True,
        )

        assert not result.ok

    def test_subtitles_disabled_when_video_disabled(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.video_enabled = False
        config.subtitles.enabled = True

        result = validate_playback_config(
            config, media_path, _media(),
            [SinkInfo("sink-a"), SinkInfo("sink-b")],
            clamp_on_error=True,
        )

        assert result.ok
        assert result.auto_clamped.get("subtitles") == {"enabled": False}
        assert config.subtitles.enabled is True  # original NOT mutated

    def test_unavailable_subtitle_track_clamped_to_zero(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.subtitles.subtitle_track = 9

        result = validate_playback_config(
            config, media_path, _media(),
            [SinkInfo("sink-a"), SinkInfo("sink-b")],
            clamp_on_error=True,
        )

        assert result.ok
        assert result.auto_clamped.get("subtitles") == {"subtitle_track": 0}
        assert config.subtitles.subtitle_track == 9  # original NOT mutated

    def test_subtitles_disabled_when_no_tracks(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.subtitles.subtitle_track = 9
        media = _media()
        media.subtitle_tracks = []

        result = validate_playback_config(
            config, media_path, media,
            [SinkInfo("sink-a"), SinkInfo("sink-b")],
            clamp_on_error=True,
        )

        assert result.ok
        assert result.auto_clamped.get("subtitles") == {"enabled": False}
        assert config.subtitles.subtitle_track == 9  # original NOT mutated

    def test_config_not_mutated_by_clamp_on_error(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.listener_b.audio_track = 9
        config.subtitles.subtitle_track = 9
        config.subtitles.enabled = True

        original_track_b = config.listener_b.audio_track
        original_sub_track = config.subtitles.subtitle_track
        original_sub_enabled = config.subtitles.enabled

        validate_playback_config(
            config, media_path, _media(),
            [SinkInfo("sink-a"), SinkInfo("sink-b")],
            clamp_on_error=True,
        )

        assert config.listener_b.audio_track == original_track_b
        assert config.subtitles.subtitle_track == original_sub_track
        assert config.subtitles.enabled == original_sub_enabled


class TestBluetooth:
    def test_is_bluetooth_sink(self) -> None:
        assert is_bluetooth_sink("bluez_output.XX_XX_XX_XX_XX_XX.1")
        assert is_bluetooth_sink("bluez_sink.aabbcc")
        assert not is_bluetooth_sink("alsa_output.pci-0000_00_1f.3.analog-stereo")
        assert not is_bluetooth_sink("")

    def test_suggest_delay_for_bluetooth_sink(self) -> None:
        assert suggest_delay_for_sink("bluez_output.XX_XX_XX_XX_XX_XX.1") == 150
        assert suggest_delay_for_sink("alsa_output.pci-0000_00_1f.3.analog-stereo") == 0

    def test_bluetooth_sink_produces_warning(self, tmp_path: Path) -> None:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"placeholder")
        config = _config()
        config.listener_a.sink = "bluez_output.XX_XX_XX_XX_XX_XX.1"

        result = validate_playback_config(
            config, media_path, _media(),
            [SinkInfo("bluez_output.XX_XX_XX_XX_XX_XX.1"), SinkInfo("sink-b")],
        )

        assert result.ok
        assert any("Bluetooth sink" in w for w in result.warnings)

    def test_clamp_video_delay(self) -> None:
        assert clamp_video_delay_ms(500) == 500
        assert clamp_video_delay_ms(-100) == 0
        assert clamp_video_delay_ms(5000) == 2000

    def test_normalize_config_includes_video_delay(self) -> None:
        config = _config()
        config.video_delay_ms = 3000
        normalize_config(config)
        assert config.video_delay_ms == 2000

    def test_video_delay_in_build_config(self) -> None:
        config = _config()
        config.video_delay_ms = 150
        assert config.video_delay_ms == 150

    def test_classify_bt_error_both_bt_listeners(self) -> None:
        from pathlib import Path as P
        from player.engine import PlaybackEngine
        config = _config()
        config.listener_a.sink = "bluez_output.AA.1"
        config.listener_b.sink = "bluez_output.BB.1"
        engine = PlaybackEngine(config, P("movie.mkv"))
        result = engine._classify_bt_error("Bluetooth link error")
        assert result is not None
        assert "bluez_output.AA.1" in result
        assert "bluez_output.BB.1" in result

    def test_classify_bt_error_no_bt_listeners(self) -> None:
        from pathlib import Path as P
        from player.engine import PlaybackEngine
        engine = PlaybackEngine(_config(), P("movie.mkv"))
        result = engine._classify_bt_error("Bluetooth link error")
        assert result is None

    def test_classify_bt_error_bt_listener_non_bt_error(self) -> None:
        from pathlib import Path as P
        from player.engine import PlaybackEngine
        config = _config()
        config.listener_a.sink = "bluez_output.AA.1"
        engine = PlaybackEngine(config, P("movie.mkv"))
        result = engine._classify_bt_error("Internal data flow error")
        assert result is None

    def test_classify_bt_error_generic_pulse_message_does_not_match(self) -> None:
        # A generic 'pa_context' / 'connection terminated' message should NOT
        # be attributed to BT just because some BT sink is configured. We
        # tightened this to reduce false positives that hid real wired errors.
        from pathlib import Path as P
        from player.engine import PlaybackEngine
        config = _config()
        config.listener_a.sink = "bluez_output.AA.1"
        engine = PlaybackEngine(config, P("movie.mkv"))
        result = engine._classify_bt_error("pa_context connect failed")
        assert result is None

    def test_classify_bt_error_matches_when_sink_named_in_message(self) -> None:
        # When the configured BT sink name appears in the error, classify it
        # as a BT-specific failure even without explicit BT keywords.
        from pathlib import Path as P
        from player.engine import PlaybackEngine
        config = _config()
        config.listener_a.sink = "bluez_output.AA.1"
        engine = PlaybackEngine(config, P("movie.mkv"))
        result = engine._classify_bt_error(
            "Resource not found: bluez_output.AA.1",
        )
        assert result == ["bluez_output.AA.1"]

    def test_suggest_delay_codec_sbc(self) -> None:
        from unittest.mock import patch
        with patch("player.devices.get_bluetooth_codec", return_value="sbc"):
            assert suggest_delay_for_sink("bluez_output.XX.1") == 200

    def test_suggest_delay_codec_aptx(self) -> None:
        from unittest.mock import patch
        with patch("player.devices.get_bluetooth_codec", return_value="aptx"):
            assert suggest_delay_for_sink("bluez_output.XX.1") == 100

    def test_suggest_delay_codec_unknown_fallback(self) -> None:
        from unittest.mock import patch
        with patch("player.devices.get_bluetooth_codec", return_value="unknown_codec"):
            assert suggest_delay_for_sink("bluez_output.XX.1") == 150

    def test_sinkinfo_is_bluetooth_sink_name(self) -> None:
        from player.models import SinkInfo
        assert SinkInfo.is_bluetooth_sink_name("bluez_output.XX.1")
        assert SinkInfo.is_bluetooth_sink_name("bluez_sink.abc")
        assert not SinkInfo.is_bluetooth_sink_name("alsa_output.pci-xxx")
        assert not SinkInfo.is_bluetooth_sink_name("")
