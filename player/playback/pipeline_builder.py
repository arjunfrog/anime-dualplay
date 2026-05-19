"""GStreamer pipeline / branch construction for PlaybackSession.

PipelineBuilder is per-build (a fresh instance is created for every
build_pipeline / rebuild). It owns the pipeline plus references to the
created branch elements (video queue, delay queue, subtitle overlay,
selector, capsfilter, selector pad map). The session reads these to
drive live operations like apply_subtitle_style or set_video_delay.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from player.models import ListenerConfig, PlayerConfig, SubtitleStyle
from player.platform import AUDIO_SINK_ELEMENT, AUDIO_SINK_DEVICE_PROPERTY, IS_MACOS
from player.validation import clamp_delay_ms, clamp_volume, is_bluetooth_sink

_logger = logging.getLogger("dual_audio_player")


StatusEmitter = Callable[[str, bool], None]


class PipelineBuilder:
    """Constructs the GStreamer pipeline and individual stream branches.

    The class is intentionally a per-build instance: it has no rebuild
    concept — the session destroys the old builder and creates a fresh
    one each time.
    """

    def __init__(
        self,
        config: PlayerConfig,
        media_path: Path,
        video_sink_element: Optional[Gst.Element] = None,
        subtitle_style: Optional[SubtitleStyle] = None,
        status_emitter: Optional[StatusEmitter] = None,
    ):
        self._config = config
        self._media_path = media_path
        self._video_sink_element = video_sink_element
        self._subtitle_style = subtitle_style or SubtitleStyle()
        self._status_emitter = status_emitter

        self.pipeline: Optional[Gst.Pipeline] = None
        self.decodebin: Optional[Gst.Element] = None

        # Branch element handles — read by the session for live operations.
        self.video_queue: Optional[Gst.Element] = None
        self.video_delay_queue: Optional[Gst.Element] = None
        self.video_sink_internal: Optional[Gst.Element] = None
        self.subtitle_overlay: Optional[Gst.Element] = None
        self.subtitle_capsfilter: Optional[Gst.Element] = None
        self.subtitle_selector: Optional[Gst.Element] = None
        self.subtitle_selector_sink_pads: dict[int, Gst.Pad] = {}

    # ------------------------------------------------------------------
    # Skeleton (filesrc + decodebin)
    # ------------------------------------------------------------------

    def build_skeleton(self) -> tuple[Gst.Pipeline, Gst.Element]:
        """Create the pipeline + filesrc + decodebin and return both
        (pipeline, decodebin). The caller wires decodebin's pad-added."""
        if not Gst.is_initialized():
            Gst.init(None)

        pipeline = Gst.Pipeline.new("dual-audio-player")
        if not pipeline:
            raise RuntimeError("Could not create GStreamer pipeline")

        source = Gst.ElementFactory.make("filesrc", "source")
        decodebin = Gst.ElementFactory.make("decodebin", "decodebin")
        if not source or not decodebin:
            raise RuntimeError(
                "Could not create filesrc/decodebin. Check GStreamer plugins."
            )

        source.set_property("location", str(self._media_path.resolve()))

        pipeline.add(source)
        pipeline.add(decodebin)
        if not source.link(decodebin):
            raise RuntimeError("Could not link filesrc to decodebin")

        self.pipeline = pipeline
        self.decodebin = decodebin
        return pipeline, decodebin

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def make_audio_branch(
        self, listener: ListenerConfig, branch_name: str,
    ) -> Gst.Element:
        assert self.pipeline is not None

        queue = Gst.ElementFactory.make("queue", f"{branch_name}_queue")
        convert = Gst.ElementFactory.make("audioconvert", f"{branch_name}_convert")
        resample = Gst.ElementFactory.make("audioresample", f"{branch_name}_resample")
        capsfilter = Gst.ElementFactory.make("capsfilter", f"{branch_name}_stereo_caps")
        volume = Gst.ElementFactory.make("volume", f"{branch_name}_volume")
        sink = Gst.ElementFactory.make(AUDIO_SINK_ELEMENT, f"{branch_name}_sink")

        if not all([queue, convert, resample, capsfilter, volume, sink]):
            raise RuntimeError(
                f"Could not create audio branch. Is the {AUDIO_SINK_ELEMENT} plugin installed?"
            )

        capsfilter.set_property("caps", Gst.Caps.from_string("audio/x-raw,channels=2"))
        volume.set_property("volume", clamp_volume(listener.volume))
        if listener.sink:
            try:
                sink.set_property(AUDIO_SINK_DEVICE_PROPERTY, listener.sink)
            except Exception as exc:
                _logger.warning(
                    "Could not set %s=%s on %s: %s",
                    AUDIO_SINK_DEVICE_PROPERTY, listener.sink, AUDIO_SINK_ELEMENT, exc,
                )
        sink.set_property("sync", True)

        if is_bluetooth_sink(listener.sink) and not IS_MACOS:
            try:
                sink.set_property("buffer-time", 50000)
                sink.set_property("latency-time", 10000)
            except Exception:
                pass

        if listener.delay_ms > 0:
            sink.set_property(
                "render-delay",
                clamp_delay_ms(listener.delay_ms) * 1_000_000,
            )
        elif listener.delay_ms < 0:
            msg = (
                f"Warning: {listener.label} has negative delay {listener.delay_ms} ms. "
                "Audio sink cannot render before the pipeline clock; use positive "
                "delay on the other branch instead."
            )
            if self._status_emitter:
                self._status_emitter(msg, False)
            _logger.warning(msg)

        for element in (queue, convert, resample, capsfilter, volume, sink):
            self.pipeline.add(element)
            element.sync_state_with_parent()

        if not queue.link(convert):
            raise RuntimeError(f"Could not link {branch_name}: queue -> audioconvert")
        if not convert.link(resample):
            raise RuntimeError(f"Could not link {branch_name}: audioconvert -> audioresample")
        if not resample.link(capsfilter):
            raise RuntimeError(f"Could not link {branch_name}: audioresample -> stereo caps")
        if not capsfilter.link(volume):
            raise RuntimeError(f"Could not link {branch_name}: stereo caps -> volume")
        if not volume.link(sink):
            raise RuntimeError(f"Could not link {branch_name}: volume -> audio sink")

        return queue

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    def ensure_video_branch(self) -> Optional[Gst.Element]:
        """Create the video branch (with optional subtitle overlay) on first
        call; return the queue input element. Returns None when video is
        disabled in config."""
        assert self.pipeline is not None
        if not self._config.video_enabled:
            return None
        if self.video_queue is not None:
            return self.video_queue

        self.video_queue = Gst.ElementFactory.make("queue", "video_queue")
        video_convert_in = Gst.ElementFactory.make("videoconvert", "video_convert_in")

        if self._video_sink_element is not None:
            sink = self._video_sink_element
        else:
            sink = Gst.ElementFactory.make(self._config.video_sink, "video_sink")

        self.video_delay_queue = Gst.ElementFactory.make("queue", "video_delay_queue")

        if not all([self.video_queue, video_convert_in, self.video_delay_queue, sink]):
            raise RuntimeError(
                f"Could not create video branch using sink {self._config.video_sink!r}"
            )

        self.video_delay_queue.set_property("max-size-buffers", 120)
        self.video_delay_queue.set_property("max-size-bytes", 0)

        if self._config.video_delay_ms > 0:
            delay_ns = self._config.video_delay_ms * 1_000_000
            self.video_delay_queue.set_property("min-threshold-time", delay_ns)
            self.video_delay_queue.set_property("max-size-time", delay_ns * 8)
        else:
            self.video_delay_queue.set_property("max-size-time", 2_000_000_000)

        self.video_sink_internal = sink

        if self._config.subtitles.enabled:
            self.subtitle_overlay = Gst.ElementFactory.make("textoverlay", "subtitle_overlay")
            video_convert_out = Gst.ElementFactory.make("videoconvert", "video_convert_out")
            if not self.subtitle_overlay or not video_convert_out:
                raise RuntimeError(
                    "Could not create textoverlay subtitle branch. "
                    "Install gstreamer1.0-plugins-base."
                )
            self.configure_subtitle_overlay(self.subtitle_overlay)
            elements: list[Gst.Element] = [
                self.video_queue, video_convert_in,
                self.subtitle_overlay, video_convert_out,
                self.video_delay_queue, sink,
            ]
        else:
            elements = [
                self.video_queue, video_convert_in,
                self.video_delay_queue, sink,
            ]

        for element in elements:
            self.pipeline.add(element)
            element.sync_state_with_parent()

        if not self.video_queue.link(video_convert_in):
            raise RuntimeError("Could not link video queue -> videoconvert")

        if self._config.subtitles.enabled:
            if not video_convert_in.link(self.subtitle_overlay):
                raise RuntimeError("Could not link videoconvert -> subtitle textoverlay")
            video_convert_out = self.pipeline.get_by_name("video_convert_out")
            if not video_convert_out:
                raise RuntimeError("Missing output videoconvert")
            if not self.subtitle_overlay.link(video_convert_out):
                raise RuntimeError("Could not link subtitle textoverlay -> output videoconvert")
            if not video_convert_out.link(self.video_delay_queue):
                raise RuntimeError("Could not link output videoconvert -> video delay queue")
            if not self.video_delay_queue.link(sink):
                raise RuntimeError("Could not link video delay queue -> video sink")
            self.ensure_subtitle_selector()
        else:
            if not video_convert_in.link(self.video_delay_queue):
                raise RuntimeError("Could not link videoconvert -> video delay queue")
            if not self.video_delay_queue.link(sink):
                raise RuntimeError("Could not link video delay queue -> video sink")

        return self.video_queue

    # ------------------------------------------------------------------
    # Subtitles
    # ------------------------------------------------------------------

    def ensure_subtitle_selector(self) -> Gst.Element:
        assert self.pipeline is not None
        if not self._config.video_enabled:
            raise RuntimeError("Cannot display subtitles because video is disabled")
        self.ensure_video_branch()
        if not self.subtitle_overlay:
            raise RuntimeError("Subtitle overlay was not created")

        if self.subtitle_selector:
            return self.subtitle_selector

        selector = Gst.ElementFactory.make("input-selector", "subtitle_selector")
        capsfilter = Gst.ElementFactory.make("capsfilter", "subtitle_text_caps")
        if not selector or not capsfilter:
            raise RuntimeError("Could not create subtitle input-selector/text capsfilter")

        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string("text/x-raw,format=(string){pango-markup,utf8}"),
        )

        for prop_name, value in (
            ("sync-streams", True),
            ("sync-mode", 1),
            ("drop-backwards", True),
        ):
            try:
                selector.set_property(prop_name, value)
            except Exception:
                pass

        for element in (selector, capsfilter):
            self.pipeline.add(element)
            element.sync_state_with_parent()

        selector_src_pad = selector.get_static_pad("src")
        caps_sink_pad = capsfilter.get_static_pad("sink")
        if not selector_src_pad or not caps_sink_pad:
            raise RuntimeError("Could not get subtitle selector/capsfilter pads")

        result = selector_src_pad.link(caps_sink_pad)
        if result != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f"Could not link subtitle selector to text capsfilter: {result.value_name}"
            )

        caps_src_pad = capsfilter.get_static_pad("src")
        overlay_text_pad = self.subtitle_overlay.get_static_pad("text_sink")
        if not caps_src_pad or not overlay_text_pad:
            pads = (
                [pad.get_name() for pad in self.subtitle_overlay.pads]
                if self.subtitle_overlay
                else []
            )
            raise RuntimeError(
                "Could not get subtitle capsfilter/overlay pads. "
                f"Available overlay pads: {pads}"
            )

        result = caps_src_pad.link(overlay_text_pad)
        if result != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f"Could not link text capsfilter to subtitle overlay: {result.value_name}"
            )

        self.subtitle_selector = selector
        self.subtitle_capsfilter = capsfilter
        return selector

    def make_subtitle_branch(self, track_index: int) -> Gst.Element:
        assert self.pipeline is not None
        selector = self.ensure_subtitle_selector()

        queue = Gst.ElementFactory.make("queue", f"subtitle_{track_index}_queue")
        if not queue:
            raise RuntimeError("Could not create subtitle queue")

        self.pipeline.add(queue)
        queue.sync_state_with_parent()

        selector_template = selector.get_pad_template("sink_%u")
        if not selector_template:
            raise RuntimeError("Subtitle selector has no request sink pad template")
        selector_sink_pad = selector.request_pad(selector_template, None, None)
        if not selector_sink_pad:
            raise RuntimeError("Could not request subtitle selector sink pad")
        try:
            selector_sink_pad.set_property("always-ok", True)
        except Exception:
            pass

        queue_src_pad = queue.get_static_pad("src")
        if not queue_src_pad:
            raise RuntimeError("Could not get subtitle queue source pad")

        result = queue_src_pad.link(selector_sink_pad)
        if result != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                f"Could not link subtitle queue to selector: {result.value_name}"
            )

        # When a subtitle pad arrives after the pipeline is already PLAYING,
        # sync_state_with_parent() above doesn't always activate the freshly
        # requested selector sink pad. Force it active so cues flow.
        try:
            selector_sink_pad.set_active(True)
        except Exception:
            pass

        self.subtitle_selector_sink_pads[track_index] = selector_sink_pad
        return queue

    def configure_subtitle_overlay(self, overlay: Gst.Element) -> None:
        """Apply the current SubtitleStyle to a textoverlay element."""
        style = self._subtitle_style
        for prop_name, value in (
            ("wait-text", False),
            ("silent", False),
            ("font-desc", style.font_desc),
            ("valignment", style.valignment),
            ("halignment", style.halignment),
            ("draw-outline", style.draw_outline),
            ("draw-shadow", style.draw_shadow),
            ("color", style.color_argb),
            ("outline-color", style.outline_color_argb),
        ):
            try:
                overlay.set_property(prop_name, value)
            except Exception:
                pass

    def apply_subtitle_style(self, style: SubtitleStyle) -> None:
        self._subtitle_style = style
        if self.subtitle_overlay is not None:
            self.configure_subtitle_overlay(self.subtitle_overlay)

    # ------------------------------------------------------------------
    # Discard
    # ------------------------------------------------------------------

    def make_discard_branch(self, label: str) -> Gst.Element:
        assert self.pipeline is not None
        queue = Gst.ElementFactory.make("queue", f"discard_{label}_queue")
        sink = Gst.ElementFactory.make("fakesink", f"discard_{label}_sink")
        if not queue or not sink:
            raise RuntimeError("Could not create discard branch")
        sink.set_property("sync", False)
        for element in (queue, sink):
            self.pipeline.add(element)
            element.sync_state_with_parent()
        if not queue.link(sink):
            raise RuntimeError("Could not link discard branch")
        return queue
