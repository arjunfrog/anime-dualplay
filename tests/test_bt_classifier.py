"""Tests for player.playback.bt_classifier — pure function, no GStreamer."""

from __future__ import annotations

from player.playback.bt_classifier import classify_bt_error


class TestNoBtSinks:
    def test_returns_none_when_no_bt_sinks(self) -> None:
        # Even an explicit BT keyword should not classify as BT when no
        # BT sink is configured — there's nothing to attribute to.
        assert classify_bt_error("bluez disconnect", []) is None

    def test_returns_none_for_empty_message_no_bt_sinks(self) -> None:
        assert classify_bt_error("", []) is None


class TestBtKeywordPath:
    def test_bluez_keyword_returns_all_bt_sinks(self) -> None:
        sinks = ["bluez_output.AA.1", "bluez_output.BB.2"]
        assert classify_bt_error("bluez bus error", sinks) == sinks

    def test_bluetooth_keyword(self) -> None:
        assert classify_bt_error("Bluetooth link lost", ["bluez_x"]) == ["bluez_x"]

    def test_a2dp_keyword(self) -> None:
        assert classify_bt_error("a2dp source unavailable", ["bluez_x"]) == ["bluez_x"]

    def test_avdtp_keyword(self) -> None:
        assert classify_bt_error("AVDTP timeout", ["bluez_x"]) == ["bluez_x"]

    def test_keyword_match_is_case_insensitive(self) -> None:
        assert classify_bt_error("BLUEZ failure", ["bluez_x"]) == ["bluez_x"]


class TestSinkNameInMessage:
    def test_sink_name_substring_classifies_as_bt(self) -> None:
        sinks = ["bluez_output.AA.1"]
        result = classify_bt_error(
            "Resource not found: bluez_output.AA.1", sinks,
        )
        assert result == ["bluez_output.AA.1"]

    def test_keyword_path_takes_precedence_over_per_sink_filter(self) -> None:
        # Once "bluez" appears in the message (whether as a keyword or
        # because it's part of a configured sink name), all configured BT
        # sinks are returned. The per-sink-name filter is only consulted
        # when no explicit BT keyword matches — in practice that's rare
        # because BT sink names contain "bluez" themselves.
        sinks = ["bluez_output.AA.1", "bluez_output.BB.2"]
        result = classify_bt_error("Sink bluez_output.AA.1 unavailable", sinks)
        assert result == sinks


class TestNoFalsePositives:
    def test_generic_pulse_message_does_not_match(self) -> None:
        # The pre-refactor implementation lumped generic pulse errors as
        # BT failures whenever a BT sink existed; the new rule rejects
        # them unless the sink is named in the message.
        assert classify_bt_error("pa_context connect failed", ["bluez_x"]) is None
        assert classify_bt_error("connection terminated", ["bluez_x"]) is None
        assert classify_bt_error("Resource not found", ["bluez_x"]) is None

    def test_unrelated_message_does_not_match(self) -> None:
        assert classify_bt_error("Internal data flow error", ["bluez_x"]) is None
