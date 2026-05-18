# Device Handling

The app targets PulseAudio or PipeWire-Pulse sinks through `pulsesink`.

## Discovery

Sink discovery first uses `Gst.DeviceMonitor`, then falls back to `pactl list short sinks`. The GTK app performs discovery on a background thread and applies results on the GTK main loop.

## Disconnection

If a sink disappears during playback, GStreamer posts an error. The engine releases the pipeline, the GUI disables playback controls, refreshes the sink list, and asks the user to choose an available output.

## Recovery

1. Reconnect the device or pick another sink.
2. Refresh outputs.
3. Restart playback.
4. Recalibrate `delayMs` if the physical output path changed.
