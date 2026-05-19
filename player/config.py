from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from player.models import PlayerConfig, ListenerConfig, SubtitleConfig, SubtitleStyle
from player.validation import clamp_delay_ms, clamp_volume, normalize_config

_logger = logging.getLogger("dual_audio_player")


def load_config(path: Path) -> PlayerConfig:
    """Load a legacy config.json or new profile-based config.
    Returns a PlayerConfig built from the active profile.
    """
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if "profiles" in raw:
        return _data_to_player_config(_resolve_active_profile_data(raw))
    return _data_to_player_config(raw)


def _resolve_active_profile_data(raw: dict) -> dict:
    """Pick the active profile dict, falling back when the named profile
    is missing so the app can still start."""
    profiles = raw.get("profiles") or {}
    active = raw.get("active_profile", "default")
    if active in profiles:
        return profiles[active]
    if "default" in profiles:
        _logger.warning(
            "active_profile %r not found; falling back to 'default'", active,
        )
        return profiles["default"]
    if profiles:
        first = next(iter(profiles))
        _logger.warning(
            "active_profile %r not found and no 'default'; using %r",
            active, first,
        )
        return profiles[first]
    _logger.warning("Config has no profiles; using empty defaults")
    return {}


def load_config_file(path: Path) -> dict:
    """Load the full config file as a dict. Creates default if missing."""
    if not path.exists():
        return _create_default_config_dict()
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return _create_default_config_dict()


def save_config_file(path: Path, data: dict) -> None:
    """Atomically write config data to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_active_profile_name(data: dict) -> str:
    return data.get("active_profile", "default")


def set_active_profile_name(data: dict, name: str) -> None:
    data["active_profile"] = name


def list_profile_names(data: dict) -> list[str]:
    profiles = data.get("profiles", {})
    if not profiles:
        return ["default"]
    return sorted(profiles.keys())


def get_profile(data: dict, name: str) -> PlayerConfig:
    profiles = data.get("profiles", {})
    if name in profiles:
        return _data_to_player_config(profiles[name])
    # Mirror load_config()'s fallback chain.
    return _data_to_player_config(_resolve_active_profile_data({
        "profiles": profiles,
        "active_profile": name,
    }))


def set_profile(data: dict, name: str, config: PlayerConfig) -> None:
    if "profiles" not in data:
        data["profiles"] = {}
    data["profiles"][name] = _player_config_to_data(config)
    data["active_profile"] = name


def delete_profile(data: dict, name: str) -> None:
    if "profiles" in data and name in data["profiles"]:
        del data["profiles"][name]
    if data.get("active_profile") == name:
        remaining = list(data.get("profiles", {}).keys())
        data["active_profile"] = remaining[0] if remaining else "default"


def save_player_config(path: Path, config: PlayerConfig) -> None:
    """Save a PlayerConfig as a legacy config.json."""
    save_config_file(path, _player_config_to_data(config))


def get_last_directory(data: dict) -> str:
    return data.get("last_directory", "")


def set_last_directory(data: dict, directory: str) -> None:
    data["last_directory"] = directory


def get_queue(data: dict) -> list[dict]:
    return data.get("queue", [])


def set_queue(data: dict, queue: list[dict]) -> None:
    data["queue"] = queue


def get_queue_index(data: dict) -> int:
    return data.get("queue_index", -1)


def set_queue_index(data: dict, index: int) -> None:
    data["queue_index"] = index


def get_sink_delay_map(data: dict) -> dict[str, int]:
    """Return the global per-sink delay map (sink name -> ms)."""
    raw = data.get("sink_delays") or {}
    out: dict[str, int] = {}
    for name, value in raw.items():
        try:
            out[str(name)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def set_sink_delay(data: dict, sink_name: str, delay_ms: int) -> None:
    if not sink_name:
        return
    delays = data.setdefault("sink_delays", {})
    delays[sink_name] = int(delay_ms)


def remove_sink_delay(data: dict, sink_name: str) -> None:
    delays = data.get("sink_delays") or {}
    delays.pop(sink_name, None)


def get_default_audio_language(data: dict) -> str:
    """Profile-independent default language preference (ISO code)."""
    return str(data.get("default_audio_language") or "")


def set_default_audio_language(data: dict, language: str) -> None:
    data["default_audio_language"] = language or ""


def get_theme_preference(data: dict) -> str:
    """Return theme preference: 'system', 'light', or 'dark'."""
    val = data.get("theme")
    if val in ("light", "dark"):
        return val
    return "system"


def set_theme_preference(data: dict, theme: str) -> None:
    if theme in ("light", "dark", "system"):
        data["theme"] = theme


def get_subtitle_style(data: dict) -> SubtitleStyle:
    raw = data.get("subtitle_style") or {}
    style = SubtitleStyle()
    if "font_desc" in raw:
        style.font_desc = str(raw["font_desc"])
    for key in ("halignment", "valignment", "color_argb", "outline_color_argb"):
        if key in raw:
            try:
                setattr(style, key, int(raw[key]))
            except (TypeError, ValueError):
                pass
    for key in ("draw_outline", "draw_shadow"):
        if key in raw:
            setattr(style, key, bool(raw[key]))
    return style


def set_subtitle_style(data: dict, style: SubtitleStyle) -> None:
    data["subtitle_style"] = {
        "font_desc": style.font_desc,
        "halignment": int(style.halignment),
        "valignment": int(style.valignment),
        "draw_outline": bool(style.draw_outline),
        "draw_shadow": bool(style.draw_shadow),
        "color_argb": int(style.color_argb),
        "outline_color_argb": int(style.outline_color_argb),
    }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _data_to_player_config(data: dict) -> PlayerConfig:
    def listener(name: str, default_label: str) -> ListenerConfig:
        ld = data.get(name, {})
        return ListenerConfig(
            label=ld.get("label", default_label),
            sink=ld.get("sink", ""),
            audio_track=int(ld.get("audioTrack", 0)),
            delay_ms=int(ld.get("delayMs", 0)),
            volume=float(ld.get("volume", 1.0)),
        )

    video = data.get("video", {})
    subtitles = data.get("subtitles", {})
    return normalize_config(PlayerConfig(
        listener_a=listener("listenerA", "Listener A"),
        listener_b=listener("listenerB", "Listener B"),
        video_enabled=bool(video.get("enabled", True)),
        video_sink=video.get("sink", "autovideosink"),
        video_delay_ms=int(video.get("delayMs", 0)),
        subtitles=SubtitleConfig(
            enabled=bool(subtitles.get("enabled", False)),
            subtitle_track=int(subtitles.get("subtitleTrack", 0)),
        ),
    ))


def _player_config_to_data(config: PlayerConfig) -> dict:
    def listener_data(lc: ListenerConfig) -> dict:
        return {
            "label": lc.label,
            "sink": lc.sink,
            "audioTrack": lc.audio_track,
            "delayMs": lc.delay_ms,
            "volume": lc.volume,
        }

    return {
        "listenerA": listener_data(config.listener_a),
        "listenerB": listener_data(config.listener_b),
        "video": {
            "enabled": config.video_enabled,
            "sink": config.video_sink,
            "delayMs": config.video_delay_ms,
        },
        "subtitles": {
            "enabled": config.subtitles.enabled,
            "subtitleTrack": config.subtitles.subtitle_track,
        },
    }


def _create_default_config_dict() -> dict:
    return {
        "version": 1,
        "last_directory": "",
        "active_profile": "default",
        "profiles": {
            "default": _player_config_to_data(PlayerConfig(
                listener_a=ListenerConfig(label="Listener A", sink="", audio_track=0),
                listener_b=ListenerConfig(label="Listener B", sink="", audio_track=1),
            )),
        },
    }


# Backward-compatible CLI override function
def merge_cli_overrides(config: PlayerConfig, args) -> PlayerConfig:
    if args.sink_a:
        config.listener_a.sink = args.sink_a
    if args.sink_b:
        config.listener_b.sink = args.sink_b
    if args.track_a is not None:
        config.listener_a.audio_track = args.track_a
    if args.track_b is not None:
        config.listener_b.audio_track = args.track_b
    if args.delay_a is not None:
        config.listener_a.delay_ms = clamp_delay_ms(args.delay_a)
    if args.delay_b is not None:
        config.listener_b.delay_ms = clamp_delay_ms(args.delay_b)
    if args.volume_a is not None:
        config.listener_a.volume = clamp_volume(args.volume_a)
    if args.volume_b is not None:
        config.listener_b.volume = clamp_volume(args.volume_b)
    if args.no_video:
        config.video_enabled = False
    if args.subtitle_track is not None:
        config.subtitles.enabled = True
        config.subtitles.subtitle_track = args.subtitle_track
    if args.no_subtitles:
        config.subtitles.enabled = False
    return normalize_config(config)
