from __future__ import annotations

import json
import subprocess
import sys
import logging
import threading
from typing import Optional, Callable

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GLib  # noqa: E402

from player.models import SinkInfo

_logger = logging.getLogger("dual_audio_player")

BT_ICON = "\U0001F50A"


def list_sinks() -> int:
    """CLI-compatible: print available sinks to stdout."""
    print("Available PulseAudio/PipeWire sinks:\n")
    sinks = get_audio_sinks()
    if sinks:
        for s in sinks:
            default_marker = " (default)" if s.is_default else ""
            print(f"  {s.name}{default_marker}")
        return 0

    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=True,
            text=True,
            capture_output=True,
        )
        print(result.stdout.rstrip() or "No sinks returned by pactl.")
        return 0
    except FileNotFoundError:
        print("pactl not found. Install pulseaudio-utils or pipewire-pulse tools.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print("pactl failed:", file=sys.stderr)
        print(exc.stderr, file=sys.stderr)
        return exc.returncode


def get_audio_sinks() -> list[SinkInfo]:
    """Programmatic audio sink discovery.

    Uses GstDeviceMonitor when possible, falling back to pactl parsing.
    Returns a list of SinkInfo objects with sink names and descriptions.
    """
    Gst.init(None)

    sinks: list[SinkInfo] = []

    # Try GstDeviceMonitor first
    monitor: Optional[Gst.DeviceMonitor] = None
    try:
        monitor = Gst.DeviceMonitor()
        monitor.add_filter("Audio/Sink", None)

        started = monitor.start()
        if started:
            devices = monitor.get_devices()
            for device in devices:
                props = device.get_properties()
                if props is None:
                    continue

                # node.name is the full PulseAudio sink name (e.g., alsa_output.pci-...)
                sink_name = props.get_string("node.name")
                if not sink_name:
                    continue

                description = props.get_string("node.description")
                if not description:
                    description = props.get_string("device.profile.description") or sink_name

                is_default = False  # GstDeviceMonitor doesn't expose default status

                sinks.append(SinkInfo(
                    name=sink_name,
                    description=description,
                    is_default=is_default,
                    sink_type=_classify_sink(sink_name),
                ))

            if sinks:
                return sinks
    except Exception as exc:
        _logger.debug("GstDeviceMonitor sink discovery failed: %s", exc)
    finally:
        if monitor is not None:
            try:
                monitor.stop()
            except Exception as exc:
                _logger.debug("GstDeviceMonitor stop failed: %s", exc)

    # Fallback: pactl list short sinks
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            check=True,
            text=True,
            capture_output=True,
            timeout=5,
        )
        sinks = _parse_pactl_output(result.stdout)
        if sinks:
            return sinks
    except Exception as exc:
        _logger.debug("pactl sink discovery failed: %s", exc)

    return sinks


def _parse_pactl_output(output: str) -> list[SinkInfo]:
    """Parse pactl list short sinks output."""
    sinks: list[SinkInfo] = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            name = parts[1].strip()
            sinks.append(SinkInfo(
                name=name,
                description=parts[0].strip(),
                sink_type=_classify_sink(name),
            ))
    return sinks


def _classify_sink(name: str) -> str:
    if name.startswith("bluez_"):
        return "bluetooth"
    if "bluez" in name.lower():
        return "bluetooth"
    if any(x in name.lower() for x in ("hdmi", "displayport")):
        return "hdmi"
    return "wired"


def get_bluetooth_codec(sink_name: str) -> Optional[str]:
    if not sink_name.startswith("bluez_output."):
        return None
    try:
        result = subprocess.run(
            ["pw-cli", "info", sink_name],
            check=False,
            text=True,
            capture_output=True,
            timeout=3,
        )
        for line in result.stdout.splitlines():
            if "api.bluez5.codec" in line:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"')
    except Exception as exc:
        _logger.debug("pw-cli codec query failed for %s: %s", sink_name, exc)
    try:
        result = subprocess.run(
            ["pw-dump", "-N", "-o", "0"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
        data = json.loads(result.stdout)
        for node in data:
            props = node.get("properties") or node.get("props") or {}
            if props.get("node.name") == sink_name:
                return props.get("api.bluez5.codec")
    except Exception as exc:
        _logger.debug("pw-dump codec query failed for %s: %s", sink_name, exc)
    return None


class DeviceMonitor:
    """Persistent audio sink monitor that emits change notifications.

    Wraps Gst.DeviceMonitor and exposes callbacks for sink list changes.
    Uses periodic polling on background threads with Gst.DeviceMonitor
    as a reliable fallback for the device-added/device-removed signals.
    All callbacks are dispatched to the GLib main loop via idle_add.
    """

    def __init__(self, poll_interval_ms: int = 3000):
        self._monitor: Optional[Gst.DeviceMonitor] = None
        self._callbacks_lock = threading.Lock()
        self._callbacks: list[Callable[[list[SinkInfo]], None]] = []
        self._poll_interval_ms = poll_interval_ms
        self._poll_timer_id: Optional[int] = None
        self._last_sink_names: set[str] = set()
        self._poll_worker_active = threading.Event()

    def start(self) -> None:
        Gst.init(None)
        try:
            self._monitor = Gst.DeviceMonitor()
            self._monitor.add_filter("Audio/Sink", None)
            self._monitor.start()
            try:
                self._monitor.connect("device-added", self._on_device_change_signal)
                self._monitor.connect("device-removed", self._on_device_change_signal)
            except Exception:
                pass
        except Exception as exc:
            _logger.debug("DeviceMonitor start failed (will poll): %s", exc)

        self._last_sink_names = {s.name for s in self._query_sinks()}
        self._start_polling()

    def stop(self) -> None:
        self._stop_polling()
        if self._monitor is not None:
            try:
                self._monitor.stop()
            except Exception:
                pass
            self._monitor = None
        with self._callbacks_lock:
            self._callbacks.clear()

    def subscribe(self, callback: Callable[[list[SinkInfo]], None]) -> None:
        with self._callbacks_lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[list[SinkInfo]], None]) -> None:
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def get_sinks(self) -> list[SinkInfo]:
        return self._query_sinks()

    def _query_sinks(self) -> list[SinkInfo]:
        if self._monitor is not None:
            try:
                devices = self._monitor.get_devices()
                sinks = self._devices_to_sink_info(devices)
                if sinks:
                    return sinks
            except Exception as exc:
                _logger.debug("Persistent GstDeviceMonitor query failed: %s", exc)
        return get_audio_sinks()

    def _devices_to_sink_info(self, devices) -> list[SinkInfo]:
        sinks: list[SinkInfo] = []
        for device in devices:
            props = device.get_properties()
            if props is None:
                continue
            sink_name = props.get_string("node.name")
            if not sink_name:
                continue
            description = props.get_string("node.description")
            if not description:
                description = props.get_string("device.profile.description") or sink_name
            sinks.append(SinkInfo(
                name=sink_name,
                description=description,
                sink_type=_classify_sink(sink_name),
            ))
        return sinks

    def _start_polling(self) -> None:
        self._stop_polling()
        try:
            self._poll_timer_id = GLib.timeout_add(
                self._poll_interval_ms,
                self._poll_sinks,
            )
        except Exception:
            pass

    def _stop_polling(self) -> None:
        if self._poll_timer_id is not None:
            try:
                GLib.source_remove(self._poll_timer_id)
            except Exception:
                pass
            self._poll_timer_id = None

    def _poll_sinks(self) -> bool:
        # Skip if a previous worker is still running so threads can't
        # accumulate when pactl/pw-cli stalls.
        if self._poll_worker_active.is_set():
            return True
        self._poll_worker_active.set()
        threading.Thread(target=self._poll_sinks_worker, daemon=True).start()
        return True

    def _poll_sinks_worker(self) -> None:
        try:
            current = self._query_sinks()
            current_names = {s.name for s in current}
            if current_names != self._last_sink_names:
                self._last_sink_names = current_names
                GLib.idle_add(self._notify, current)
        finally:
            self._poll_worker_active.clear()

    def _on_device_change_signal(self, monitor: Gst.DeviceMonitor, device: Gst.Device) -> None:
        threading.Thread(target=self._device_change_worker, daemon=True).start()

    def _device_change_worker(self) -> None:
        current = self._query_sinks()
        current_names = {s.name for s in current}
        self._last_sink_names = current_names
        GLib.idle_add(self._notify, current)

    def _notify(self, sinks: list[SinkInfo]) -> None:
        with self._callbacks_lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(sinks)
            except Exception as exc:
                _logger.debug("DeviceMonitor callback error: %s", exc)
