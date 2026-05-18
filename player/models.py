from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListenerConfig:
    label: str
    sink: str
    audio_track: int
    delay_ms: int = 0
    volume: float = 1.0


@dataclass
class SubtitleStyle:
    """Look-and-feel for the textoverlay element. Stored globally (not per
    profile) since users typically want one consistent style."""
    font_desc: str = "Sans Bold 18"
    halignment: int = 1  # 0=left, 1=center, 2=right
    valignment: int = 1  # 0=top, 1=baseline (bottom-ish), 2=bottom, 3=center
    draw_outline: bool = True
    draw_shadow: bool = True
    color_argb: int = 0xFFFFFFFF  # opaque white
    outline_color_argb: int = 0xFF000000  # opaque black


@dataclass
class SubtitleConfig:
    enabled: bool = False
    subtitle_track: int = 0


@dataclass
class PlayerConfig:
    listener_a: ListenerConfig
    listener_b: ListenerConfig
    video_enabled: bool = True
    video_sink: str = "autovideosink"
    video_delay_ms: int = 0
    subtitles: SubtitleConfig = field(default_factory=SubtitleConfig)


@dataclass
class TrackInfo:
    index: int
    global_index: int
    type: str
    codec: str
    language: str
    title: str = ""
    channels: int = 0
    sample_rate: int = 0
    stream_id: str = ""

    @property
    def display_label(self) -> str:
        codec_str = self.codec
        if self.channels:
            codec_str += f" {self.channels}.0"
        lang_str = f"{self.language} - " if self.language else ""
        return f"{self.type.capitalize()} {self.index}: {lang_str}{codec_str}"


@dataclass
class MediaInfo:
    uri: str
    duration: float
    audio_tracks: list[TrackInfo] = field(default_factory=list)
    video_tracks: list[TrackInfo] = field(default_factory=list)
    subtitle_tracks: list[TrackInfo] = field(default_factory=list)
    container_format: str = ""
    seekable: bool = True


@dataclass
class SinkInfo:
    name: str
    description: str = ""
    is_default: bool = False
    sink_type: str = "wired"

    @property
    def is_bluetooth(self) -> bool:
        return self.sink_type == "bluetooth"

    @classmethod
    def is_bluetooth_sink_name(cls, sink_name: str) -> bool:
        return sink_name.startswith("bluez_") or "bluez" in sink_name.lower()
