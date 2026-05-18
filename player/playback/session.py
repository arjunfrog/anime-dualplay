"""Playback lifecycle, bus handling, position polling, and live operations.

PlaybackSession is the public surface that replaces the former monolithic
PlaybackEngine. It owns:
  * config, media path, observer/callbacks
  * the per-pipeline PipelineBuilder + PadRouter (rebuilt on every play)
  * the GLib main loop (headless mode), bus signal watch, position timer
  * subtitle style (so it survives pipeline rebuilds)

All branch construction and pad routing live in pipeline_builder.py and
pad_router.py respectively; this module wires them together and exposes
the player.engine API.
"""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GLib  # noqa: E402

from player.models import MediaInfo, PlayerConfig, SubtitleStyle
from player.playback.bt_classifier import classify_bt_error
from player.playback.pad_router import PadRouter
from player.playback.pipeline_builder import PipelineBuilder
from player.validation import clamp_delay_ms, clamp_volume, is_bluetooth_sink

_logger = logging.getLogger("dual_audio_player")


@dataclass
class PlaybackObserver:
    """Bundle of optional callbacks the engine emits to. Pass to
    PlaybackSession.set_observer() to wire all events at once."""
    on_position: Optional[Callable[[float, float], None]] = None
    on_status: Optional[Callable[[str, bool], None]] = None
    on_eos: Optional[Callable[[], None]] = None
    on_pads: Optional[Callable[[bool, bool, bool, bool], None]] = None
    on_error: Optional[Callable[[str], None]] = None


class PlaybackSession:
    """GStreamer-based dual-audio video player.

    Headless mode runs its own GLib.MainLoop and uses an autovideosink (or
    a config-supplied sink). GUI mode expects the caller to provide a
    video sink element (and optionally a video_sink_factory) and runs no
    internal main loop.
    """

    def __init__(
        self,
        config: PlayerConfig,
        media_path: Path,
        video_sink_element: Optional[Gst.Element] = None,
        headless: bool = True,
        media_info: Optional[MediaInfo] = None,
    ):
        self.config = config
        self.media_path = media_path
        self._video_sink_element = video_sink_element
        self.headless = headless
        self.media_info = media_info

        self._builder: Optional[PipelineBuilder] = None
        self._router: Optional[PadRouter] = None

        self.pipeline: Optional[Gst.Pipeline] = None
        self.loop: Optional[GLib.MainLoop] = None
        self._bus: Optional[Gst.Bus] = None
        self._bus_handler_id: Optional[int] = None
        self._bus_watch_active = False
        self._startup_timer_id: Optional[int] = None
        self._position_timer_id: Optional[int] = None
        self._playback_generation = 0
        self._stopped = True
        self.is_paused = False

        self._subtitle_style: SubtitleStyle = SubtitleStyle()
        self._last_bt_disconnect_sink: Optional[list[str]] = None

        # Callbacks for GUI integration.
        self._on_position: Optional[Callable[[float, float], None]] = None
        self._on_status: Optional[Callable[[str, bool], None]] = None
        self._on_eos: Optional[Callable[[], None]] = None
        self._on_pads: Optional[Callable[[bool, bool, bool, bool], None]] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._video_sink_factory: Optional[Callable[[], Optional[Gst.Element]]] = None

    # ------------------------------------------------------------------
    # Public — diagnostics
    # ------------------------------------------------------------------

    @property
    def last_bt_disconnect_sinks(self) -> Optional[list[str]]:
        return self._last_bt_disconnect_sink

    # ------------------------------------------------------------------
    # Public — pad/connection accessors (read by the GUI / tests)
    # ------------------------------------------------------------------

    @property
    def listener_a_connected(self) -> bool:
        return self._router.listener_a_connected if self._router else False

    @property
    def listener_b_connected(self) -> bool:
        return self._router.listener_b_connected if self._router else False

    @property
    def video_connected(self) -> bool:
        return self._router.video_connected if self._router else False

    @property
    def subtitle_connected(self) -> bool:
        return self._router.subtitle_connected if self._router else False

    # ------------------------------------------------------------------
    # Public — callback wiring
    # ------------------------------------------------------------------

    def set_position_callback(
        self, callback: Optional[Callable[[float, float], None]],
    ) -> None:
        self._on_position = callback

    def set_status_callback(
        self, callback: Optional[Callable[[str, bool], None]],
    ) -> None:
        self._on_status = callback

    def set_eos_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._on_eos = callback

    def set_pads_callback(
        self, callback: Optional[Callable[[bool, bool, bool, bool], None]],
    ) -> None:
        self._on_pads = callback

    def set_error_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        self._on_error = callback

    def set_video_sink_factory(
        self, factory: Optional[Callable[[], Optional[Gst.Element]]],
    ) -> None:
        """Provide a factory the engine can call to obtain a fresh video sink
        on rebuild. Required for GUI mode so the destroyed sink is replaced."""
        self._video_sink_factory = factory

    def set_observer(self, observer: PlaybackObserver) -> None:
        """Wire all engine event callbacks at once."""
        self._on_position = observer.on_position
        self._on_status = observer.on_status
        self._on_eos = observer.on_eos
        self._on_pads = observer.on_pads
        self._on_error = observer.on_error

    # ------------------------------------------------------------------
    # Pipeline construction
    # ------------------------------------------------------------------

    def build_pipeline(self) -> None:
        if self.pipeline:
            self.destroy_pipeline()

        self._reset_state()

        builder = PipelineBuilder(
            config=self.config,
            media_path=self.media_path,
            video_sink_element=self._video_sink_element,
            subtitle_style=self._subtitle_style,
            status_emitter=self._emit_status,
        )
        pipeline, decodebin = builder.build_skeleton()

        router = PadRouter(
            builder=builder,
            config=self.config,
            media_info=self.media_info,
            status_emitter=self._emit_status,
            on_fatal=lambda _msg: self.stop(error=True),
        )
        decodebin.connect("pad-added", router.on_pad_added)

        self._builder = builder
        self._router = router
        self.pipeline = pipeline

        bus = pipeline.get_bus()
        if bus:
            self._bus = bus
            bus.add_signal_watch()
            self._bus_watch_active = True
            self._bus_handler_id = bus.connect("message", self._on_bus_message)

    # ------------------------------------------------------------------
    # Subtitle style (live)
    # ------------------------------------------------------------------

    def apply_subtitle_style(self, style: SubtitleStyle) -> None:
        self._subtitle_style = style
        if self._builder is not None:
            self._builder.apply_subtitle_style(style)

    # ------------------------------------------------------------------
    # Playback lifecycle
    # ------------------------------------------------------------------

    def play(self) -> int:
        if not self.pipeline:
            self.build_pipeline()

        assert self.pipeline is not None
        self._stopped = False
        self._playback_generation += 1
        generation = self._playback_generation

        if self.headless:
            self.loop = GLib.MainLoop()
            # Signal handlers run on whatever thread the kernel chose; bus
            # and GLib operations must happen on the main loop thread.
            signal.signal(signal.SIGINT, lambda *_: GLib.idle_add(self.stop))
            signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(self.stop))
        else:
            self._start_position_timer()

        _logger.info("Starting playback.")
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            msg = "Failed to start playback."
            _logger.error(msg)
            self._emit_status(msg, True)
            # Tear down without entering the GLib main loop.
            self._cancel_startup_timer()
            self._stop_position_timer()
            self._teardown_bus()
            self.pipeline.set_state(Gst.State.NULL)
            self._stopped = True
            self.loop = None
            return 1

        self._cancel_startup_timer()
        self._startup_timer_id = GLib.timeout_add_seconds(
            3, self._report_startup_status, generation,
        )
        self._report_startup_diagnostics()

        if self.headless and self.loop:
            self.loop.run()
            self.destroy_pipeline()
            return 0
        return 0

    def pause(self) -> None:
        if not self.pipeline:
            return
        self.is_paused = True
        self.pipeline.set_state(Gst.State.PAUSED)
        _logger.info("Paused.")

    def resume(self) -> None:
        if not self.pipeline:
            return
        self.is_paused = False
        self.pipeline.set_state(Gst.State.PLAYING)
        _logger.info("Playing.")

    def toggle_pause(self) -> None:
        if not self.pipeline:
            return
        if self.is_paused:
            self.resume()
        else:
            self.pause()

    def stop(self, error: bool = False) -> None:
        if self._stopped and not self.pipeline:
            return
        self._stopped = True
        self._cancel_startup_timer()
        self._stop_position_timer()
        self._teardown_bus()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        if self.headless and self.loop and self.loop.is_running():
            self.loop.quit()
        if error and self.loop:
            GLib.idle_add(lambda: self.loop.quit() if self.loop else False)

    def destroy_pipeline(self) -> None:
        self.stop()
        self._stop_position_timer()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        self._teardown_bus()
        self._builder = None
        self._router = None
        # Drop the external sink reference so the next build_pipeline()
        # requires the caller to set a fresh one. This prevents a destroyed
        # sink from being re-added to a new pipeline.
        self._video_sink_element = None
        self._reset_state()

    def _reset_state(self) -> None:
        self.is_paused = False
        self._stopped = True

    # ------------------------------------------------------------------
    # Bus + timers
    # ------------------------------------------------------------------

    def _on_bus_message(self, bus: Gst.Bus, message: Gst.Message) -> None:
        msg_type = message.type

        if msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            err_msg = str(err)
            msg = f"GStreamer error: {err}"
            _logger.error(msg)
            if debug:
                _logger.debug("GStreamer error debug: %s", debug)
            bt_sinks = self._classify_bt_error(err_msg)
            if bt_sinks is not None:
                msg = f"Bluetooth device disconnected: {err}"
                self._last_bt_disconnect_sink = bt_sinks
            self._emit_status(msg, True)
            if self._on_error:
                self._on_error(msg)
            self.stop(error=True)

        elif msg_type == Gst.MessageType.EOS:
            _logger.info("End of stream.")
            if self._on_eos:
                self._on_eos()
            self.stop()

        elif msg_type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            msg = f"GStreamer warning: {warn}"
            _logger.warning(msg)
            if debug:
                _logger.debug("GStreamer warning debug: %s", debug)
            self._emit_status(msg, False)

        elif msg_type == Gst.MessageType.ELEMENT:
            struct = message.get_structure()
            if struct and struct.get_name() == "missing-plugin":
                msg = f"Missing GStreamer plugin: {struct.to_string()}"
                _logger.error(msg)
                self._emit_status(msg, True)

        elif msg_type == Gst.MessageType.LATENCY:
            if self.pipeline:
                self.pipeline.recalculate_latency()
                try:
                    _, latency = self.pipeline.query_latency()
                    self._emit_status(
                        f"Pipeline latency: {latency / Gst.MSECOND:.0f} ms", False,
                    )
                except Exception:
                    self._emit_status("Pipeline latency recalculated.", False)

    def _classify_bt_error(self, err_msg: str) -> Optional[list[str]]:
        bt_sinks = [
            lc.sink for lc in (self.config.listener_a, self.config.listener_b)
            if is_bluetooth_sink(lc.sink)
        ]
        return classify_bt_error(err_msg, bt_sinks)

    def _cancel_startup_timer(self) -> None:
        if self._startup_timer_id is not None:
            try:
                GLib.source_remove(self._startup_timer_id)
            except Exception:
                pass
            self._startup_timer_id = None

    def _teardown_bus(self) -> None:
        if self._bus is not None:
            try:
                self._bus.set_flushing(True)
            except Exception:
                pass
            if self._bus_handler_id is not None:
                try:
                    self._bus.disconnect(self._bus_handler_id)
                except Exception:
                    pass
                self._bus_handler_id = None
            if self._bus_watch_active:
                try:
                    self._bus.remove_signal_watch()
                except Exception:
                    pass
                self._bus_watch_active = False
        self._bus = None

    def _emit_status(self, message: str, is_error: bool) -> None:
        if self._on_status:
            self._on_status(message, is_error)
        elif is_error:
            # Without an observer (e.g. early pipeline construction in
            # headless --quiet mode) errors would otherwise vanish.
            _logger.error(message)

    # ------------------------------------------------------------------
    # Position / duration
    # ------------------------------------------------------------------

    def get_position(self) -> float:
        if not self.pipeline:
            return 0.0
        success, position = self.pipeline.query_position(Gst.Format.TIME)
        return position / Gst.SECOND if success else 0.0

    def get_duration(self) -> float:
        if not self.pipeline:
            return 0.0
        success, duration = self.pipeline.query_duration(Gst.Format.TIME)
        return duration / Gst.SECOND if success else 0.0

    def seek(self, seconds: float, accurate: bool = True) -> bool:
        if not self.pipeline:
            return False
        new_pos = max(0, int(seconds * Gst.SECOND))
        flags = Gst.SeekFlags.FLUSH | (
            Gst.SeekFlags.ACCURATE if accurate else Gst.SeekFlags.KEY_UNIT
        )
        accepted = self.pipeline.seek_simple(Gst.Format.TIME, flags, new_pos)
        if not accepted:
            msg = f"Seek to {seconds:.3f}s was rejected by GStreamer."
            _logger.warning(msg)
            self._emit_status(msg, True)
        return bool(accepted)

    def seek_relative(self, seconds: float) -> None:
        if not self.pipeline:
            return
        pos = self.get_position()
        self.seek(pos + seconds)

    # ------------------------------------------------------------------
    # Live adjustments (no rebuild)
    # ------------------------------------------------------------------

    def set_video_sink_element(self, element: Gst.Element) -> None:
        self._video_sink_element = element

    def set_listener_volume(self, listener_id: str, volume: float) -> None:
        if not self.pipeline:
            return
        element = self.pipeline.get_by_name(f"{listener_id}_volume")
        if element:
            element.set_property("volume", clamp_volume(volume))

    def set_listener_delay(self, listener_id: str, delay_ms: int) -> None:
        delay_ms = clamp_delay_ms(delay_ms)
        if listener_id == "listener_a":
            self.config.listener_a.delay_ms = delay_ms
        elif listener_id == "listener_b":
            self.config.listener_b.delay_ms = delay_ms
        if not self.pipeline:
            return
        element = self.pipeline.get_by_name(f"{listener_id}_sink")
        if element:
            element.set_property("render-delay", delay_ms * 1_000_000)

    def set_video_delay(self, delay_ms: int) -> None:
        delay_ms = clamp_delay_ms(delay_ms)
        self.config.video_delay_ms = delay_ms
        if not self.pipeline:
            return
        element = self.pipeline.get_by_name("video_delay_queue")
        if element:
            if delay_ms > 0:
                delay_ns = delay_ms * 1_000_000
                element.set_property("min-threshold-time", delay_ns)
                element.set_property("max-size-time", delay_ns * 8)
            else:
                element.set_property("min-threshold-time", 0)
                element.set_property("max-size-time", 2_000_000_000)

    # ------------------------------------------------------------------
    # Operations requiring pipeline rebuild
    # ------------------------------------------------------------------

    def set_listener_track(self, listener_id: str, track_index: int) -> None:
        if listener_id == "listener_a":
            self.config.listener_a.audio_track = track_index
        elif listener_id == "listener_b":
            self.config.listener_b.audio_track = track_index
        self._rebuild_pipeline()

    def set_listener_sink(self, listener_id: str, sink_name: str) -> None:
        if listener_id == "listener_a":
            self.config.listener_a.sink = sink_name
        elif listener_id == "listener_b":
            self.config.listener_b.sink = sink_name
        self._rebuild_pipeline()

    def set_subtitle_enabled(self, enabled: bool) -> None:
        self.config.subtitles.enabled = enabled
        self._rebuild_pipeline()

    def set_subtitle_track(self, track_index: int) -> None:
        self.config.subtitles.subtitle_track = track_index
        if not self.config.subtitles.enabled:
            return
        if self._builder is not None and self._builder.subtitle_selector and self._router is not None:
            if self._router.activate_subtitle_track(track_index, asynchronous=True):
                return
            # Pad hasn't arrived yet — defer instead of forcing a rebuild.
            self._router.pending_subtitle_track = track_index
            self._emit_status(
                f"Subtitle track {track_index} will be selected when it becomes available.",
                False,
            )
            return
        # No selector yet (e.g. subtitles were disabled when pipeline built).
        self._rebuild_pipeline()

    def _rebuild_pipeline(self) -> None:
        """Rebuild pipeline preserving position. In GUI mode, asks the
        configured video_sink_factory for a fresh sink BEFORE tearing the
        old pipeline down — if the factory fails the current pipeline is
        left intact and the caller sees a status/error message."""
        was_playing = not self.is_paused and self.pipeline is not None
        position = self.get_position()

        new_sink: Optional[Gst.Element] = None
        if not self.headless:
            if self._video_sink_factory is not None:
                try:
                    new_sink = self._video_sink_factory()
                except Exception as exc:
                    msg = f"Pipeline rebuild aborted: video_sink_factory raised: {exc}"
                    _logger.error(msg)
                    self._emit_status(msg, True)
                    if self._on_error:
                        self._on_error(msg)
                    return
            if new_sink is None:
                msg = "Pipeline rebuild aborted: no video sink available"
                _logger.error(msg)
                self._emit_status(msg, True)
                if self._on_error:
                    self._on_error(msg)
                return

        self.destroy_pipeline()

        if not self.headless:
            self._video_sink_element = new_sink

        self.build_pipeline()

        if not self.pipeline:
            return

        self.pipeline.set_state(Gst.State.PAUSED)
        if position > 0:
            self.seek(position, accurate=True)

        state = Gst.State.PLAYING if was_playing else Gst.State.PAUSED
        self.pipeline.set_state(state)
        self.is_paused = not was_playing
        if not self.headless:
            self._start_position_timer()
        self._stopped = False

    # ------------------------------------------------------------------
    # Startup status reporting
    # ------------------------------------------------------------------

    def _report_startup_status(self, generation: Optional[int] = None) -> bool:
        if generation is not None and generation != self._playback_generation:
            return False
        self._startup_timer_id = None
        missing = []
        if not self.listener_a_connected:
            missing.append(f"listener A audio track {self.config.listener_a.audio_track}")
        if not self.listener_b_connected:
            missing.append(f"listener B audio track {self.config.listener_b.audio_track}")
        if self.config.video_enabled and not self.video_connected:
            missing.append("video")
        if self.config.subtitles.enabled and not self.subtitle_connected:
            missing.append(f"subtitle track {self.config.subtitles.subtitle_track}")
        if missing:
            msg = "Startup check: not linked yet or unavailable: " + ", ".join(missing)
            _logger.warning(msg)
            self._emit_status(msg, True)
            _logger.info(
                "If an E-AC-3/AC3 track is missing, install/check gstreamer1.0-libav."
            )
        if self._on_pads:
            self._on_pads(
                self.listener_a_connected,
                self.listener_b_connected,
                self.video_connected,
                self.subtitle_connected,
            )
        return False

    def _report_startup_diagnostics(self) -> None:
        details = [
            "Startup diagnostics:",
            f"  {self.config.listener_a.label}: track {self.config.listener_a.audio_track}, "
            f"sink {self.config.listener_a.sink}, delay {self.config.listener_a.delay_ms} ms, "
            "stereo PCM output",
            f"  {self.config.listener_b.label}: track {self.config.listener_b.audio_track}, "
            f"sink {self.config.listener_b.sink}, delay {self.config.listener_b.delay_ms} ms, "
            "stereo PCM output",
            f"  video: delay {self.config.video_delay_ms} ms",
        ]
        if self.media_info:
            for track in self.media_info.audio_tracks:
                details.append(
                    f"  source audio {track.index}: {track.codec}, "
                    f"{track.channels or '?'} channels, "
                    f"{track.sample_rate or '?'} Hz"
                )
        _logger.info("\n".join(details))

    # ------------------------------------------------------------------
    # Position timer (GUI mode)
    # ------------------------------------------------------------------

    def _start_position_timer(self) -> None:
        if self.headless:
            return
        if self._position_timer_id is None:
            self._position_timer_id = GLib.timeout_add(250, self._emit_position)

    def _stop_position_timer(self) -> None:
        if self._position_timer_id is not None:
            GLib.source_remove(self._position_timer_id)
            self._position_timer_id = None

    def _emit_position(self) -> bool:
        if self._on_position:
            self._on_position(self.get_position(), self.get_duration())
        return True
