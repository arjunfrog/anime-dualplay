"""Tests for player.platform — cross-platform detection and abstraction."""

from __future__ import annotations

import sys
from unittest.mock import patch

from player.platform import (
    IS_LINUX,
    IS_MACOS,
    AUDIO_SINK_DEVICE_PROPERTY,
    AUDIO_SINK_ELEMENT,
    PREFERRED_VIDEO_SINKS,
    get_log_directory,
    has_pactl,
    has_pw_cli,
)


class TestPlatformDetection:
    def test_exactly_one_platform_is_true(self) -> None:
        assert IS_MACOS != IS_LINUX or (not IS_MACOS and not IS_LINUX)
        if sys.platform == "darwin":
            assert IS_MACOS
            assert not IS_LINUX
        elif sys.platform.startswith("linux"):
            assert IS_LINUX
            assert not IS_MACOS


class TestAudioSinkConstants:
    def test_macos_uses_osxaudiosink(self) -> None:
        if IS_MACOS:
            assert AUDIO_SINK_ELEMENT == "osxaudiosink"
            assert AUDIO_SINK_DEVICE_PROPERTY == "unique-id"

    def test_linux_uses_pulsesink(self) -> None:
        if IS_LINUX:
            assert AUDIO_SINK_ELEMENT == "pulsesink"
            assert AUDIO_SINK_DEVICE_PROPERTY == "device"


class TestVideoSinkConstants:
    def test_macos_prefers_gtksink(self) -> None:
        if IS_MACOS:
            assert "gtksink" in PREFERRED_VIDEO_SINKS
            assert "xvimagesink" not in PREFERRED_VIDEO_SINKS

    def test_linux_prefers_xvimagesink(self) -> None:
        if IS_LINUX:
            assert "xvimagesink" in PREFERRED_VIDEO_SINKS
            assert "gtksink" not in PREFERRED_VIDEO_SINKS

    def test_autovideosink_always_in_list(self) -> None:
        assert "autovideosink" in PREFERRED_VIDEO_SINKS


class TestLogDirectory:
    def test_macos_uses_library_path(self) -> None:
        if IS_MACOS:
            log_dir = get_log_directory()
            assert "Library/Application Support" in log_dir
            assert "dual-audio-player" in log_dir

    def test_linux_uses_local_share(self) -> None:
        if IS_LINUX:
            log_dir = get_log_directory()
            assert ".local/share" in log_dir
            assert "dual-audio-player" in log_dir


class TestToolAvailability:
    def test_pactl_false_on_macos(self) -> None:
        if IS_MACOS:
            assert has_pactl() is False

    def test_pw_cli_false_on_macos(self) -> None:
        if IS_MACOS:
            assert has_pw_cli() is False

    def test_pactl_queries_which_on_linux(self) -> None:
        if IS_LINUX:
            result = has_pactl()
            assert isinstance(result, bool)
