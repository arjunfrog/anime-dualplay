"""Platform detection and abstraction for cross-platform support.

Provides helper functions and constants that let the rest of the codebase
work on both Linux (PulseAudio/PipeWire) and macOS (CoreAudio) without
scattering sys.platform checks everywhere.
"""

from __future__ import annotations

import sys

IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

AUDIO_SINK_ELEMENT = "osxaudiosink" if IS_MACOS else "pulsesink"
AUDIO_SINK_DEVICE_PROPERTY = "unique-id" if IS_MACOS else "device"

PREFERRED_VIDEO_SINKS = (
    ["gtksink", "glimagesink", "autovideosink"]
    if IS_MACOS
    else ["xvimagesink", "ximagesink", "autovideosink"]
)


def get_log_directory() -> str:
    """Return the platform-appropriate log directory path."""
    from pathlib import Path

    if IS_MACOS:
        return str(Path.home() / "Library" / "Application Support" / "dual-audio-player")
    return str(Path.home() / ".local" / "share" / "dual-audio-player")


def has_pactl() -> bool:
    """Return True if pactl is available (Linux with PulseAudio/PipeWire)."""
    if IS_MACOS:
        return False
    import shutil
    return shutil.which("pactl") is not None


def has_pw_cli() -> bool:
    """Return True if pw-cli / pw-dump are available (PipeWire)."""
    if IS_MACOS:
        return False
    import shutil
    return shutil.which("pw-cli") is not None
