# Testing

## Automated Checks

Run:

```bash
python3 -m compileall .
python3 -m pytest
```

## Manual Playback Matrix

- Two different audio tracks routed to two different sinks.
- Same audio track routed to both sinks.
- Sink change during playback preserves position.
- Track change during playback preserves position.
- Subtitle track change preserves position when subtitles are already enabled.
- Subtitle enable/disable may rebuild playback, but should restore the previous position.
- Slider seek uses accurate seeking; subtitles should resume at timestamps that contain subtitle cues.
- EOS releases devices.
- USB DAC unplug during playback stops cleanly and refreshes sinks.
- Multi-channel source downmixes predictably to stereo.

Use a synthetic MKV with two obvious audio tracks and at least one subtitle track when possible.

## Subtitle Seek Checks

With the provided sample files:

- Open `test_files/test1.mkv`, start with subtitle track 0, then switch to tracks 1, 2, and back to 0. Playback position should not reset.
- Subtitle switching should not pause, hang, or perform a visible seek; text may appear at the next cue on sparse subtitle streams.
- In `test_files/test1.mkv`, drag the slider near `10:38`; subtitle cues should resume when the selected track has text there.
- In `test_files/test1.mkv`, drag near `10:00`; no subtitle text can be normal until the next cue if the selected track has a gap.
- Open `test_files/test2.mkv`, seek around `10:00`, and confirm subtitle display continues after the seek.

## Bluetooth Audio Checks

- Play video with Bluetooth headphones as Listener B; verify lipsync with `video_delay_ms=200` (adjust value based on active Bluetooth codec).
- Unplug/disable Bluetooth adapter during playback; verify clean error message mentioning "Bluetooth device".
- Power off Bluetooth headphones during playback; verify status shows BT-specific error and sink list refreshes automatically.
- Power on Bluetooth headphones after disconnect; verify sink reappears in combo box within ~3 seconds.
- Change Bluetooth codec (aptX → SBC via WirePlumber config); verify different delay values are needed for lipsync.
- Pair a new Bluetooth device while the app is running; verify it appears in the sink list without manual refresh.
- Verify Bluetooth sinks display with a speaker icon and "(BT)" suffix in the UI sink dropdowns.
- Verify video delay spin button appears between Listener B and Subtitle panels; set a delay value and confirm it persists across app restart.
- Verify selecting a Bluetooth sink triggers a warning in the status bar about latency and suggested video delay.
