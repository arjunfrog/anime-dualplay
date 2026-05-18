#!/usr/bin/env python3
"""
dual_audio_player.py

Prototype dual-audio video player for Linux.

Goal:
  - Play one video file.
  - Send one selected audio stream to physical output A.
  - Send another selected audio stream to physical output B.
  - Optionally display one selected subtitle stream over the video.
  - Keep video, subtitles, and both audio branches in the same GStreamer pipeline/clock.

Designed for:
  - PipeWire/PulseAudio sinks via pulsesink.
  - Local MKV/MP4 files with multiple audio tracks.

Controls while playing:
  space / p  = pause/resume
  q          = quit
  left/right = seek -10/+10 seconds, if terminal supports it
  h / l      = seek -10/+10 seconds fallback keys
"""

from __future__ import annotations

import argparse
import os
import select
import signal
import sys
import termios
import threading
import tty
from pathlib import Path

from player.config import load_config, merge_cli_overrides
from player.devices import get_audio_sinks, list_sinks
from player.inspector import discover_media, inspect_media
from player.engine import PlaybackEngine
from player.validation import validate_playback_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play one video while routing two audio tracks to two different physical outputs."
    )
    parser.add_argument("file", nargs="?", help="Media file to play")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--list-sinks", action="store_true", help="List PulseAudio/PipeWire sinks and exit")
    parser.add_argument("--inspect", metavar="FILE", help="Inspect media streams and exit")
    parser.add_argument("--sink-a", help="Pulse/PipeWire sink name for listener A")
    parser.add_argument("--sink-b", help="Pulse/PipeWire sink name for listener B")
    parser.add_argument("--track-a", type=int, help="Audio track index for listener A, zero-based in decoded audio order")
    parser.add_argument("--track-b", type=int, help="Audio track index for listener B, zero-based in decoded audio order")
    parser.add_argument("--subtitle-track", type=int, help="Enable subtitles and select subtitle index, zero-based in decoded subtitle order")
    parser.add_argument("--no-subtitles", action="store_true", help="Disable subtitles even if enabled in config")
    parser.add_argument("--delay-a", type=int, help="Listener A positive output delay in ms")
    parser.add_argument("--delay-b", type=int, help="Listener B positive output delay in ms")
    parser.add_argument("--volume-a", type=float, help="Listener A volume, 1.0 is normal")
    parser.add_argument("--volume-b", type=float, help="Listener B volume, 1.0 is normal")
    parser.add_argument("--no-video", action="store_true", help="Disable video output")
    parser.add_argument("--quiet", action="store_true", help="Suppress engine status messages on stderr")
    return parser.parse_args()


class _TerminalControls:
    """Reads single keypresses from the terminal on a background thread."""

    def __init__(self, engine: PlaybackEngine):
        self._engine = engine
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not sys.stdin.isatty():
            print("No TTY detected; keyboard controls disabled.")
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _loop(self) -> None:
        import gi
        from gi.repository import GLib

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while not self._stop.is_set():
                readable, _, _ = select.select([fd], [], [], 0.1)
                if not readable:
                    continue

                data = os.read(fd, 16)
                if not data:
                    continue

                if b"\x1b[C" in data:
                    GLib.idle_add(self._engine.seek_relative, 10)
                    continue
                if b"\x1b[D" in data:
                    GLib.idle_add(self._engine.seek_relative, -10)
                    continue

                for byte in data:
                    ch = chr(byte)
                    if ch in ("q", "Q", "\x03"):
                        print("\nQuit requested.", flush=True)
                        GLib.idle_add(self._engine.stop)
                        return
                    if ch in (" ", "p", "P"):
                        GLib.idle_add(self._engine.toggle_pause)
                    elif ch in ("l", "L"):
                        GLib.idle_add(self._engine.seek_relative, 10)
                    elif ch in ("h", "H"):
                        GLib.idle_add(self._engine.seek_relative, -10)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main() -> int:
    args = parse_args()

    if args.list_sinks:
        return list_sinks()

    if args.inspect:
        return inspect_media(Path(args.inspect))

    if not args.file:
        print("No media file supplied. Use --help for usage.", file=sys.stderr)
        return 2

    media_path = Path(args.file)
    if not media_path.exists():
        print(f"File not found: {media_path}", file=sys.stderr)
        return 1

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    config = merge_cli_overrides(load_config(config_path), args)
    try:
        media_info = discover_media(media_path)
    except Exception as exc:
        print(f"Media discovery failed: {exc}", file=sys.stderr)
        return 1

    sinks = get_audio_sinks()
    validation = validate_playback_config(config, media_path, media_info, sinks)
    if validation.warnings:
        for warning in validation.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
    if not validation.ok:
        for error in validation.errors:
            print(f"Config error: {error}", file=sys.stderr)
        return 1

    print("Dual audio routing:")
    print(f"  {config.listener_a.label}: audio track {config.listener_a.audio_track} -> {config.listener_a.sink}")
    print(f"  {config.listener_b.label}: audio track {config.listener_b.audio_track} -> {config.listener_b.sink}")
    print(f"  Video: {'enabled' if config.video_enabled else 'disabled'} via {config.video_sink}")
    if config.subtitles.enabled:
        print(f"  Subtitles: enabled, subtitle track {config.subtitles.subtitle_track}")
    else:
        print("  Subtitles: disabled")

    engine = PlaybackEngine(config, media_path, media_info=media_info)

    if not args.quiet:
        def _emit_status(msg: str, is_err: bool) -> None:
            prefix = "ERROR: " if is_err else ""
            print(f"{prefix}{msg}", file=sys.stderr, flush=True)
        engine.set_status_callback(_emit_status)
        engine.set_error_callback(lambda msg: _emit_status(msg, True))

    controls = _TerminalControls(engine)
    controls.start()
    print("Starting playback. Keep this terminal focused for keyboard controls. Press space/p to pause, q to quit, left/right or h/l to seek.")

    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)

    result = engine.play()
    controls.stop()
    return result


if __name__ == "__main__":
    sys.exit(main())
