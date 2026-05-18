# Troubleshooting

## No Audio

- Confirm the selected sink appears in `./dual_audio_player.py --list-sinks`.
- Confirm each listener has a non-empty sink in `config.json`.
- Check that the selected audio track index exists for the media file.

## Wrong Language

Run media inspection and compare audio tracks. Playback prefers discovered stream IDs, but if a file lacks stable IDs it falls back to decoded pad order and warns.

## Missing Plugin

Install the recommended GStreamer plugin packages from `README.md`, especially `gstreamer1.0-libav` for AC-3/E-AC-3 and other codecs.

## Subtitles

- Changing subtitle tracks while subtitles are enabled should preserve playback position.
- Track switching is in-place, asynchronous, and should not perform a seek. On sparse subtitle streams, the newly selected track may not show text until its next cue.
- Raw text subtitle streams must negotiate as `text/x-raw` with `pango-markup` or `utf8` format before reaching the overlay.
- Slider seeks use accurate seeking so subtitle cues can resume after jumping to a new timestamp.
- Some source files have subtitle gaps. If no cue exists at the current timestamp, no text appears until the next cue.
- If a subtitle track fails to switch, check the status bar or terminal output for a selector/rebuild warning.

## Drift Or Offset

Use positive `delayMs` on the earlier output. If a USB DAC is late, delay the other listener. Long-term independent hardware drift is not automatically corrected.

## Device Disconnected

Reconnect the device, refresh sinks, select an available output, and restart playback.

## Fullscreen

- Press `F11` to enter or exit fullscreen.
- Press `Escape` to exit fullscreen.
- Double-click the video area to toggle fullscreen.
- Right-click the video area to open an enter/exit fullscreen menu.

If the window manager changes fullscreen state externally, the app should restore the controls, routing panels, subtitle panel and status bar when fullscreen ends.
