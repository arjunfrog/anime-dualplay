from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from player.models import MediaInfo, PlayerConfig, SinkInfo

MIN_VOLUME = 0.0
MAX_VOLUME = 2.0
MIN_DELAY_MS = 0
MAX_DELAY_MS = 2000
BT_DELAY_SUGGESTION_MS = 150

BT_CODEC_DELAY_MS: dict[str, int] = {
    "sbc": 200,
    "aac": 160,
    "aptx": 100,
    "aptx_hd": 100,
    "aptx_ll": 40,
    "ldac": 180,
    "lc3": 30,
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_clamped: dict[str, dict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def clamp_volume(volume: float) -> float:
    return min(MAX_VOLUME, max(MIN_VOLUME, float(volume)))


def clamp_delay_ms(delay_ms: int) -> int:
    return min(MAX_DELAY_MS, max(MIN_DELAY_MS, int(delay_ms)))


def clamp_video_delay_ms(delay_ms: int) -> int:
    return clamp_delay_ms(delay_ms)


def normalize_config(config: PlayerConfig) -> PlayerConfig:
    """Clamp all config fields to valid ranges. Mutates and returns the same object."""
    config.listener_a.volume = clamp_volume(config.listener_a.volume)
    config.listener_b.volume = clamp_volume(config.listener_b.volume)
    config.listener_a.delay_ms = clamp_delay_ms(config.listener_a.delay_ms)
    config.listener_b.delay_ms = clamp_delay_ms(config.listener_b.delay_ms)
    config.video_delay_ms = clamp_video_delay_ms(config.video_delay_ms)
    config.listener_a.audio_track = max(0, int(config.listener_a.audio_track))
    config.listener_b.audio_track = max(0, int(config.listener_b.audio_track))
    config.subtitles.subtitle_track = max(0, int(config.subtitles.subtitle_track))
    return config


def validate_playback_config(
    config: PlayerConfig,
    media_path: Path,
    media_info: MediaInfo | None,
    sinks: list[SinkInfo] | None,
    clamp_on_error: bool = False,
) -> ValidationResult:
    result = ValidationResult()
    if not media_path.exists():
        result.errors.append(f"Media file does not exist: {media_path}")

    if sinks is not None and not sinks:
        if clamp_on_error:
            result.warnings.append("No audio output sinks discovered; playback may produce no audio.")
        else:
            result.errors.append("No audio output sinks were discovered.")

    sink_names = {sink.name for sink in sinks or []}
    _validate_listener("Listener A", "listener_a", config.listener_a.sink, config.listener_a.audio_track, media_info, sink_names, result, clamp_on_error)
    _validate_listener("Listener B", "listener_b", config.listener_b.sink, config.listener_b.audio_track, media_info, sink_names, result, clamp_on_error)

    if config.subtitles.enabled:
        if not config.video_enabled:
            if clamp_on_error:
                result.warnings.append("Subtitles require video output; disabling subtitles.")
                result.auto_clamped["subtitles"] = {"enabled": False}
            else:
                result.errors.append("Subtitles require video output to be enabled.")
        subtitle_count = len(media_info.subtitle_tracks) if media_info else 0
        if media_info is not None and config.subtitles.subtitle_track >= subtitle_count:
            if clamp_on_error:
                if subtitle_count > 0:
                    result.warnings.append(f"Subtitle track {config.subtitles.subtitle_track} unavailable; using track 0.")
                    result.auto_clamped["subtitles"] = {"subtitle_track": 0}
                else:
                    result.warnings.append(f"Subtitles disabled; media has no subtitle tracks.")
                    result.auto_clamped["subtitles"] = {"enabled": False}
            else:
                result.errors.append(
                    f"Subtitle track {config.subtitles.subtitle_track} is unavailable; media has {subtitle_count} subtitle tracks."
                )

    if media_info is not None and not media_info.audio_tracks:
        result.errors.append("Media discovery found no audio tracks.")

    if config.listener_a.audio_track == config.listener_b.audio_track:
        result.warnings.append(
            f"Both listeners use audio track {config.listener_a.audio_track}; playback will fan out that stream."
        )

    _validate_bluetooth_sinks(config, sinks, result)

    return result


def _validate_listener(
    label: str,
    listener_key: str,
    sink_name: str,
    track_index: int,
    media_info: MediaInfo | None,
    sink_names: set[str],
    result: ValidationResult,
    clamp_on_error: bool = False,
) -> None:
    if not sink_name:
        if clamp_on_error:
            result.warnings.append(f"{label} has no output sink selected; audio may not be audible.")
        else:
            result.errors.append(f"{label} has no output sink selected.")
    elif sink_names and sink_name not in sink_names:
        if clamp_on_error:
            result.warnings.append(f"{label} sink is not available: {sink_name}")
        else:
            result.errors.append(f"{label} sink is not currently available: {sink_name}")

    audio_count = len(media_info.audio_tracks) if media_info else 0
    if media_info is not None and track_index >= audio_count:
        if clamp_on_error and audio_count > 0:
            result.warnings.append(f"{label} audio track {track_index} unavailable; clamped to track 0.")
            result.auto_clamped[listener_key] = {"audio_track": 0}
        else:
            result.errors.append(f"{label} audio track {track_index} is unavailable; media has {audio_count} audio tracks.")


def is_bluetooth_sink(sink_name: str) -> bool:
    return SinkInfo.is_bluetooth_sink_name(sink_name)


def _validate_bluetooth_sinks(
    config: PlayerConfig,
    sinks: list[SinkInfo] | None,
    result: ValidationResult,
) -> None:
    if not sinks:
        return
    bt_sinks_in_use = []
    for listener, label in ((config.listener_a, "Listener A"), (config.listener_b, "Listener B")):
        if is_bluetooth_sink(listener.sink):
            bt_sinks_in_use.append((label, listener.sink))
    for label, sink_name in bt_sinks_in_use:
        result.warnings.append(
            f"{label} uses Bluetooth sink — may add 100–250ms latency. "
            f"Consider setting video delay to {BT_DELAY_SUGGESTION_MS}ms."
        )


def suggest_delay_for_sink(sink_name: str) -> int:
    if not is_bluetooth_sink(sink_name):
        return 0
    try:
        from player.devices import get_bluetooth_codec
        codec = get_bluetooth_codec(sink_name)
        if codec and codec.lower() in BT_CODEC_DELAY_MS:
            return BT_CODEC_DELAY_MS[codec.lower()]
    except Exception:
        pass
    return BT_DELAY_SUGGESTION_MS
