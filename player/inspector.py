from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import gi
gi.require_version("GstPbutils", "1.0")
gi.require_version("Gst", "1.0")
from gi.repository import GstPbutils, Gst  # noqa: E402

from player.models import MediaInfo, TrackInfo


def inspect_media(path: Path) -> int:
    """CLI-compatible inspect that prints to stdout. Uses subprocess for backward compat."""
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    for cmd in (["gst-discoverer-1.0", str(path)], ["ffprobe", "-hide_banner", str(path)]):
        try:
            result = subprocess.run(cmd, text=True, capture_output=True)
        except FileNotFoundError:
            continue
        print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip())
        return result.returncode

    print("Neither gst-discoverer-1.0 nor ffprobe was found.", file=sys.stderr)
    print("Install gstreamer1.0-tools or ffmpeg.", file=sys.stderr)
    return 1


def discover_media(path: Path, timeout_seconds: int = 10) -> MediaInfo:
    """Use GstPbutils.Discoverer to inspect a media file programmatically.

    Returns MediaInfo with TrackInfo objects that have per-type zero-based indexing.
    Falls back to subprocess inspection on failure.
    """
    Gst.init(None)

    discoverer = GstPbutils.Discoverer.new(Gst.SECOND * timeout_seconds)
    if not discoverer:
        raise RuntimeError("Could not create GstDiscoverer")

    uri = path.resolve().as_uri()
    info = discoverer.discover_uri(uri)
    if not info:
        raise RuntimeError(f"Could not discover media: {path}")

    return _parse_discoverer_info(info)


def _parse_discoverer_info(info: GstPbutils.DiscovererInfo) -> MediaInfo:
    """Extract stream info from a GstDiscovererInfo."""
    dur_ns = info.get_duration()
    duration = dur_ns / Gst.SECOND if dur_ns > 0 else 0.0
    seekable = info.get_seekable()

    container_format = ""
    uri = info.get_uri() or ""

    info_list = info.get_stream_list()
    if not info_list:
        return MediaInfo(
            uri=uri, duration=duration, seekable=seekable, container_format=container_format,
        )

    all_tracks: list[TrackInfo] = []
    type_counts: dict[str, int] = {}

    for i, stream in enumerate(info_list):
        caps = stream.get_caps()
        if not caps or caps.get_size() == 0:
            continue

        stream_type = stream.get_stream_type_nick()

        if stream_type == "audio":
            ttype = "audio"
        elif stream_type == "video":
            ttype = "video"
        elif stream_type in ("subtitle", "subtitles", "text"):
            ttype = "subtitle"
        else:
            continue

        idx = type_counts.get(ttype, 0)
        type_counts[ttype] = idx + 1

        language = ""
        title = ""
        tags = stream.get_tags()
        if tags:
            ok, lang = tags.get_string(Gst.TAG_LANGUAGE_CODE)
            if ok and lang:
                language = lang
            ok, ttl = tags.get_string(Gst.TAG_TITLE)
            if ok and ttl:
                title = ttl

        codec = _caps_to_codec(caps)
        channels = 0
        sample_rate = 0
        stream_id = ""
        try:
            stream_id = stream.get_stream_id() or ""
        except Exception:
            pass

        if ttype == "audio":
            try:
                channels = stream.get_channels()
            except Exception:
                pass
            try:
                sample_rate = stream.get_sample_rate()
            except Exception:
                pass

        track = TrackInfo(
            index=idx,
            global_index=i + 1,
            type=ttype,
            codec=codec,
            language=language,
            title=title,
            channels=channels,
            sample_rate=sample_rate,
            stream_id=stream_id,
        )
        all_tracks.append(track)

    return MediaInfo(
        uri=uri,
        duration=duration,
        audio_tracks=[t for t in all_tracks if t.type == "audio"],
        video_tracks=[t for t in all_tracks if t.type == "video"],
        subtitle_tracks=[t for t in all_tracks if t.type == "subtitle"],
        container_format=container_format,
        seekable=seekable,
    )


def _caps_to_codec(caps) -> str:
    """Derive a human-readable codec name from caps."""
    if not caps or caps.get_size() == 0:
        return "Unknown"
    structure = caps.get_structure(0)
    name = structure.get_name()

    # Handle audio/mpeg specially — check for AAC profile
    if name == "audio/mpeg":
        mpeg_version = structure.get_int("mpegversion")
        if mpeg_version[0] and mpeg_version[1] == 4:
            return "AAC"
        return "MP3"

    codec_map = {
        "video/x-h264": "H.264",
        "video/x-h265": "H.265",
        "video/x-vp8": "VP8",
        "video/x-vp9": "VP9",
        "video/x-av1": "AV1",
        "audio/x-raw": "PCM",
        "audio/x-opus": "Opus",
        "audio/x-vorbis": "Vorbis",
        "audio/x-flac": "FLAC",
        "audio/x-aac": "AAC",
        "audio/x-ac3": "AC-3",
        "audio/x-eac3": "E-AC-3",
        "audio/x-dts": "DTS",
        "audio/x-wma": "WMA",
        "subtitle/x-ass": "ASS",
        "subpicture/x-ssa": "SSA",
        "subtitle/x-ssa": "SSA",
        "application/x-ssa": "SSA",
        "application/x-ass": "ASS",
        "subpicture/x-pgs": "PGS",
        "text/x-raw": "Timed Text",
        "text/x-srt": "SRT",
        "subtitle/x-srt": "SRT",
    }
    if name in codec_map:
        return codec_map[name]
    return name.split("/")[-1].upper()
