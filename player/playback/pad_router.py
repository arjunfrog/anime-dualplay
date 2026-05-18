"""Pad-added dispatcher for PlaybackSession.

PadRouter is per-pipeline (a fresh instance is created for every build).
It owns the transient routing state — audio/subtitle pad counters,
connection flags, the pending subtitle track — and delegates branch
construction to a PipelineBuilder.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GLib  # noqa: E402

from player.models import ListenerConfig, MediaInfo, PlayerConfig
from player.playback.pipeline_builder import PipelineBuilder

_logger = logging.getLogger("dual_audio_player")


StatusEmitter = Callable[[str, bool], None]
FatalCallback = Callable[[str], None]


class PadRouter:
    """Routes decodebin pads onto the appropriate branches created via the
    given PipelineBuilder. Tracks per-pipeline state used by startup
    reporting and live subtitle switching.
    """

    def __init__(
        self,
        builder: PipelineBuilder,
        config: PlayerConfig,
        media_info: Optional[MediaInfo],
        status_emitter: Optional[StatusEmitter] = None,
        on_fatal: Optional[FatalCallback] = None,
    ):
        self._builder = builder
        self._config = config
        self._media_info = media_info
        self._status_emitter = status_emitter
        self._on_fatal = on_fatal

        # Per-pipeline routing state.
        self.audio_pad_index = 0
        self.subtitle_pad_index = 0
        self.video_connected = False
        self.subtitle_connected = False
        self.listener_a_connected = False
        self.listener_b_connected = False
        self.pending_subtitle_track: Optional[int] = None

    # ------------------------------------------------------------------
    # Public — wire to decodebin's pad-added
    # ------------------------------------------------------------------

    def on_pad_added(self, decodebin: Gst.Element, pad: Gst.Pad) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0) if caps and caps.get_size() > 0 else None
        media_type = structure.get_name() if structure else "unknown"

        try:
            if media_type.startswith("audio/"):
                fallback_index = self.audio_pad_index
                self.audio_pad_index += 1
                # fallback_index assumes decodebin emits pads in container order;
                # stream-ID matching via _track_index_for_pad should succeed for
                # modern containers (MKV, MP4). The fallback is a safety net only.
                current_index = self._track_index_for_pad(pad, "audio", fallback_index)
                self._link_audio_to_listeners(pad, current_index)

            elif media_type.startswith("video/") and not self.video_connected:
                queue = self._builder.ensure_video_branch()
                if queue:
                    self._link_pad_to_queue(pad, queue, "video", None)
                    self.video_connected = True
                else:
                    _logger.info("Video disabled; ignoring video stream.")

            elif self._is_subtitle_media_type(media_type):
                fallback_index = self.subtitle_pad_index
                self.subtitle_pad_index += 1
                current_index = self._track_index_for_pad(
                    pad, "subtitle", fallback_index,
                )

                if self._config.subtitles.enabled:
                    queue = self._builder.make_subtitle_branch(current_index)
                    self._link_pad_to_queue(pad, queue, "subtitle", current_index)
                    if (
                        current_index == self._config.subtitles.subtitle_track
                        or current_index == self.pending_subtitle_track
                    ):
                        self.activate_subtitle_track(current_index, announce=False)
                    else:
                        _logger.info(
                            f"Linked available subtitle track {current_index}: {media_type}"
                        )
                else:
                    _logger.info(
                        f"Discarding subtitle track index {current_index}; "
                        f"subtitles are disabled: {media_type}"
                    )
                    queue = self._builder.make_discard_branch(
                        f"subtitle_{current_index}"
                    )
                    self._link_pad_to_queue(
                        pad, queue, "discarded subtitle", current_index,
                    )

            else:
                _logger.info(f"Discarding unsupported stream: {media_type}")
                queue = self._builder.make_discard_branch(
                    f"unsupported_{pad.get_name().replace('-', '_')}"
                )
                self._link_pad_to_queue(pad, queue, "discarded stream", None)

        except Exception as exc:
            msg = f"Failed while handling pad {pad.get_name()} ({media_type}): {exc}"
            _logger.error(msg)
            self._emit_status(msg, True)
            if self._on_fatal:
                self._on_fatal(msg)

    # ------------------------------------------------------------------
    # Subtitle activation
    # ------------------------------------------------------------------

    def activate_subtitle_track(
        self,
        track_index: int,
        announce: bool = True,
        asynchronous: bool = False,
    ) -> bool:
        selector_pad = self._builder.subtitle_selector_sink_pads.get(track_index)
        selector = self._builder.subtitle_selector
        if selector is None or selector_pad is None:
            self.pending_subtitle_track = track_index
            return False

        self.subtitle_connected = True
        self.pending_subtitle_track = None
        if asynchronous and hasattr(selector, "call_async"):
            captured_selector = selector

            def switch_active_pad(element: Gst.Element, _user_data: object) -> None:
                # Bail out if a pipeline rebuild swapped the selector under us.
                if self._builder.subtitle_selector is not captured_selector:
                    return
                try:
                    element.set_property("active-pad", selector_pad)
                except Exception as exc:
                    GLib.idle_add(
                        self._emit_subtitle_switch_status,
                        f"Subtitle track {track_index} switch failed: {exc}",
                        True,
                        captured_selector,
                    )
                    return
                if announce:
                    GLib.idle_add(
                        self._emit_subtitle_switch_status,
                        f"Subtitle track {track_index} selected.",
                        False,
                        captured_selector,
                    )

            selector.call_async(switch_active_pad, None)
            return True

        selector.set_property("active-pad", selector_pad)
        if announce:
            self._emit_status(f"Subtitle track {track_index} selected.", False)
        return True

    def all_subtitle_pads_seen(self) -> bool:
        if not self._media_info:
            return False
        expected_indexes = {
            track.index for track in self._media_info.subtitle_tracks
        }
        if not expected_indexes:
            return True
        return expected_indexes.issubset(
            self._builder.subtitle_selector_sink_pads,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_subtitle_switch_status(
        self,
        message: str,
        is_error: bool,
        selector: Gst.Element,
    ) -> bool:
        if self._builder.subtitle_selector is selector:
            self._emit_status(message, is_error)
        return False

    def _emit_status(self, message: str, is_error: bool) -> None:
        if self._status_emitter:
            self._status_emitter(message, is_error)

    @staticmethod
    def _is_subtitle_media_type(media_type: str) -> bool:
        return (
            media_type.startswith("text/")
            or media_type.startswith("subtitle/")
            or media_type.startswith("subpicture/")
            or "subtitle" in media_type
            or "x-ass" in media_type
            or "x-ssa" in media_type
        )

    def _track_index_for_pad(
        self, pad: Gst.Pad, kind: str, fallback_index: int,
    ) -> int:
        stream_id = ""
        try:
            stream_id = pad.get_stream_id() or ""
        except Exception:
            pass

        tracks = []
        if self._media_info and kind == "audio":
            tracks = self._media_info.audio_tracks
        elif self._media_info and kind == "subtitle":
            tracks = self._media_info.subtitle_tracks

        if stream_id and tracks:
            for track in tracks:
                if track.stream_id == stream_id:
                    return track.index
            self._emit_status(
                f"Could not match {kind} stream id {stream_id!r}; "
                "falling back to decoded pad order.",
                False,
            )
        return fallback_index

    def _link_audio_to_listeners(self, pad: Gst.Pad, track_index: int) -> None:
        targets: list[tuple[str, ListenerConfig]] = []
        if track_index == self._config.listener_a.audio_track:
            targets.append(("listener_a", self._config.listener_a))
        if track_index == self._config.listener_b.audio_track:
            targets.append(("listener_b", self._config.listener_b))

        if not targets:
            _logger.info(f"Discarding unselected audio track index {track_index}")
            queue = self._builder.make_discard_branch(f"audio_{track_index}")
            self._link_pad_to_queue(pad, queue, "discarded audio", track_index)
            return

        if len(targets) == 1:
            branch_name, listener = targets[0]
            queue = self._builder.make_audio_branch(listener, branch_name)
            self._link_pad_to_queue(pad, queue, listener.label, track_index)
        else:
            self._link_pad_to_audio_tee(pad, targets, track_index)

        for branch_name, _listener in targets:
            if branch_name == "listener_a":
                self.listener_a_connected = True
            elif branch_name == "listener_b":
                self.listener_b_connected = True

    def _link_pad_to_audio_tee(
        self,
        pad: Gst.Pad,
        targets: list[tuple[str, ListenerConfig]],
        track_index: int,
    ) -> None:
        pipeline = self._builder.pipeline
        assert pipeline is not None
        tee = Gst.ElementFactory.make("tee", f"audio_{track_index}_tee")
        if not tee:
            raise RuntimeError("Could not create audio tee for duplicate listener routing")
        pipeline.add(tee)
        tee.sync_state_with_parent()

        tee_sink = tee.get_static_pad("sink")
        if not tee_sink:
            raise RuntimeError("Audio tee has no sink pad")
        result = pad.link(tee_sink)
        if result != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f"Could not link audio track {track_index} to tee: {result.value_name}"
            )

        for branch_name, listener in targets:
            queue = self._builder.make_audio_branch(listener, branch_name)
            queue_sink = queue.get_static_pad("sink")
            if not queue_sink:
                raise RuntimeError(f"No sink pad available for {listener.label}")

            template = tee.get_pad_template("src_%u")
            if not template:
                raise RuntimeError("Audio tee has no request src pad template")
            tee_src = tee.request_pad(template, None, None)
            if not tee_src:
                raise RuntimeError("Could not request audio tee source pad")
            result = tee_src.link(queue_sink)
            if result != Gst.PadLinkReturn.OK:
                raise RuntimeError(
                    f"Could not link tee to {listener.label}: {result.value_name}"
                )
            _logger.info(f"Linked {listener.label} track {track_index} via tee")

    def _link_pad_to_queue(
        self,
        pad: Gst.Pad,
        queue: Gst.Element,
        label: str,
        track_index: Optional[int],
    ) -> None:
        sink_pad = queue.get_static_pad("sink")
        if not sink_pad:
            raise RuntimeError(f"No sink pad available for {label}")
        caps = pad.get_current_caps() or pad.query_caps(None)
        caps_text = caps.to_string() if caps else "unknown caps"
        result = pad.link(sink_pad)
        if result != Gst.PadLinkReturn.OK:
            raise RuntimeError(f"Could not link {label} pad: {result.value_name}")
        suffix = f" track {track_index}" if track_index is not None else ""
        _logger.info(f"Linked {label}{suffix}: {caps_text}")
