"""Tests for the non-profile, global pieces of the config dict:
sink_delays, default_audio_language, subtitle_style, and the
active_profile fallback chain."""

from __future__ import annotations

from player.config import (
    get_sink_delay_map,
    set_sink_delay,
    remove_sink_delay,
    get_default_audio_language,
    set_default_audio_language,
    get_subtitle_style,
    set_subtitle_style,
    get_profile,
)
from player.models import SubtitleStyle


class TestSinkDelayMap:
    def test_empty_map_when_unset(self) -> None:
        assert get_sink_delay_map({}) == {}

    def test_set_and_get(self) -> None:
        data: dict = {}
        set_sink_delay(data, "alsa_output.foo", 120)
        set_sink_delay(data, "bluez_output.bar", 200)
        m = get_sink_delay_map(data)
        assert m == {"alsa_output.foo": 120, "bluez_output.bar": 200}

    def test_set_ignores_empty_sink_name(self) -> None:
        data: dict = {}
        set_sink_delay(data, "", 100)
        assert get_sink_delay_map(data) == {}

    def test_remove(self) -> None:
        data: dict = {}
        set_sink_delay(data, "x", 50)
        remove_sink_delay(data, "x")
        assert get_sink_delay_map(data) == {}

    def test_get_skips_non_int_values(self) -> None:
        data = {"sink_delays": {"x": "not-an-int", "y": 75}}
        assert get_sink_delay_map(data) == {"y": 75}


class TestDefaultAudioLanguage:
    def test_empty_default(self) -> None:
        assert get_default_audio_language({}) == ""

    def test_round_trip(self) -> None:
        data: dict = {}
        set_default_audio_language(data, "jpn")
        assert get_default_audio_language(data) == "jpn"

    def test_clearing(self) -> None:
        data = {"default_audio_language": "eng"}
        set_default_audio_language(data, "")
        assert get_default_audio_language(data) == ""


class TestSubtitleStyle:
    def test_defaults_when_unset(self) -> None:
        style = get_subtitle_style({})
        assert style.draw_outline is True
        assert style.draw_shadow is True
        assert style.color_argb == 0xFFFFFFFF

    def test_round_trip(self) -> None:
        data: dict = {}
        original = SubtitleStyle(
            font_desc="Mono 22",
            halignment=0,
            valignment=2,
            draw_outline=False,
            draw_shadow=False,
            color_argb=0xCCFFFF00,
            outline_color_argb=0x99000000,
        )
        set_subtitle_style(data, original)
        loaded = get_subtitle_style(data)
        assert loaded.font_desc == "Mono 22"
        assert loaded.halignment == 0
        assert loaded.valignment == 2
        assert loaded.draw_outline is False
        assert loaded.draw_shadow is False
        assert loaded.color_argb == 0xCCFFFF00
        assert loaded.outline_color_argb == 0x99000000

    def test_partial_data_keeps_defaults(self) -> None:
        loaded = get_subtitle_style({"subtitle_style": {"font_desc": "Sans 12"}})
        assert loaded.font_desc == "Sans 12"
        assert loaded.draw_outline is True
        assert loaded.halignment == 1


class TestActiveProfileFallback:
    def _data_with_profiles(self, *names: str, active: str | None = None) -> dict:
        profiles = {n: {} for n in names}
        return {
            "profiles": profiles,
            "active_profile": active if active is not None else names[0],
        }

    def test_get_profile_returns_named_profile(self) -> None:
        data = self._data_with_profiles("a", "b")
        # Doesn't raise even though profile dict is empty.
        config = get_profile(data, "a")
        assert config.listener_a.audio_track == 0

    def test_get_profile_falls_back_to_default(self) -> None:
        data = self._data_with_profiles("default", "other")
        config = get_profile(data, "missing")
        assert config is not None  # falls back to default

    def test_get_profile_falls_back_to_first_when_no_default(self) -> None:
        data = self._data_with_profiles("only_one")
        config = get_profile(data, "missing")
        assert config is not None
