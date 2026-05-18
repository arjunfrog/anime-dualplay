# Dual Audio Player Prototype

Prototype Linux player for one video with two audio tracks routed to two different physical outputs, plus optional subtitle display over the video.

Example target setup:

```text
ThinkPad onboard headphone jack  -> listener A -> Japanese
USB DAC headphone output          -> listener B -> English
Laptop screen / HDMI              -> video + selected subtitle track
```

This uses one GStreamer pipeline, so video, subtitles and both audio branches share the same playback clock.

The project now includes both a CLI entry point (`dual_audio_player.py`) and a GTK UI (`app.py`) for selecting media, profiles, tracks, sinks, subtitles and playback controls.

## Project documentation

- [Agent instructions](AGENTS.md)
- [Steering guidelines](CLAUDE.md)
- [Development log](DEVLOG.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Configuration](docs/CONFIGURATION.md)
- [Playback sync](docs/PLAYBACK_SYNC.md)
- [Device handling](docs/DEVICE_HANDLING.md)
- [Testing](docs/TESTING.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Remediation audit](docs/REMEDIATION_AUDIT.md)

## Install dependencies

Ubuntu/Debian:

```bash
sudo apt update
sudo apt install \
  python3 \
  python3-gi \
  python3-gst-1.0 \
  gir1.2-gstreamer-1.0 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  pipewire-pulse \
  pavucontrol \
  pulseaudio-utils
```

If AV1 video does not display, make sure `gstreamer1.0-libav` is installed. On some systems, hardware AV1 decode may still be awkward; testing with an H.264/H.265 MKV first is useful.

## List audio outputs

```bash
./dual_audio_player.py --list-sinks
```

You should see something like:

```text
alsa_output.pci-0000_00_1f.3.analog-stereo
alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo
```

Copy the exact sink names into `config.json`.

## Inspect a media file

```bash
./dual_audio_player.py --inspect /path/to/movie.mkv
```

This uses `gst-discoverer-1.0` if available, or `ffprobe` if installed.

Important: the inspect output numbers all streams globally, for example:

```text
video #1
audio #2: ja
audio #3: en
subtitles #4: en
subtitles #5: enm
subtitles #6: en
```

But the player config uses zero-based order **within each stream type**:

```text
Audio config:
  audioTrack 0 = first audio stream  = inspect audio #2 = ja
  audioTrack 1 = second audio stream = inspect audio #3 = en

Subtitle config:
  subtitleTrack 0 = first subtitle stream  = inspect subtitles #4
  subtitleTrack 1 = second subtitle stream = inspect subtitles #5
  subtitleTrack 2 = third subtitle stream  = inspect subtitles #6
```

## Configure outputs and subtitles

Edit `config.json`:

```json
{
  "listenerA": {
    "label": "Listener A",
    "sink": "alsa_output.pci-0000_00_1f.3.analog-stereo",
    "audioTrack": 0,
    "delayMs": 0,
    "volume": 1.0
  },
  "listenerB": {
    "label": "Listener B",
    "sink": "alsa_output.usb-YOUR_USB_DAC_NAME.analog-stereo",
    "audioTrack": 1,
    "delayMs": 0,
    "volume": 1.0
  },
  "video": {
    "enabled": true,
    "sink": "autovideosink"
  },
  "subtitles": {
    "enabled": true,
    "subtitleTrack": 0
  }
}
```

For your example file:

```text
audio #2 ja -> audioTrack 0
audio #3 en -> audioTrack 1
subtitles #4 -> subtitleTrack 0
subtitles #5 -> subtitleTrack 1
subtitles #6 -> subtitleTrack 2
```

## Play

```bash
./dual_audio_player.py /path/to/movie.mkv
```

GTK UI:

```bash
./app.py
```

Or override config from CLI:

```bash
./dual_audio_player.py /path/to/movie.mkv \
  --track-a 0 \
  --sink-a alsa_output.pci-0000_00_1f.3.analog-stereo \
  --track-b 1 \
  --sink-b alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo \
  --subtitle-track 0
```

Disable subtitles temporarily:

```bash
./dual_audio_player.py /path/to/movie.mkv --no-subtitles
```

Disable video temporarily:

```bash
./dual_audio_player.py /path/to/movie.mkv --no-video
```

## Keyboard controls

While playing:

```text
space / p  pause/resume
q          quit
right      seek +10 seconds
left       seek -10 seconds
```

Arrow keys depend on the terminal.

## Delay / sync adjustment

Each output can have a positive delay in milliseconds:

```json
"delayMs": 100
```

That delays that listener's audio by 100 ms.

Negative delay is not truly possible at the sink because an output cannot play before the pipeline clock. If one output feels late, add positive delay to the other output instead.

For example, if the USB DAC is 80 ms later than onboard audio, set:

```json
"listenerA": { "delayMs": 80 }
"listenerB": { "delayMs": 0 }
```

## Profiles

`config.json` supports multiple named profiles under `profiles`, with one
`active_profile`. Use the **Profiles** menu in the GTK UI to:

- Switch between profiles.
- Save the current routing as a new profile.
- Delete a profile.
- **Import / Export** a single profile as JSON for sharing across machines.

If the named `active_profile` is missing from disk the app falls back to
`default` (or the first available profile) and logs a warning.

## Playlist queue

Files added via the right-side **Playlist** sidebar play in order. The queue
is persisted to `config.json` and survives restarts. The previous/next track
buttons in the transport bar (and the `AudioNext`/`AudioPrev` media keys)
move within the queue.

## Per-sink remembered delays

Whenever you adjust a listener's **Delay** spinbox, the value is saved
against the currently selected sink in the global `sink_delays` map. The
next time you pick the same physical output (in any profile) and its
listener delay is `0`, the saved delay is auto-filled. This is independent
of the per-profile delay so you can carry calibrated offsets across setups.

## Default audio language

**Settings → Default Audio Language…** lets you set an ISO code (e.g.
`eng`, `jpn`). When you open a file whose track index is out of range,
the player falls back to the first track matching that language.

## Subtitle style

**Settings → Subtitle Style…** opens a dialog for font, position,
alignment, text colour, outline colour, and whether shadow/outline draw.
Changes apply live to the currently playing subtitle overlay.

## Keyboard shortcuts (GTK)

```text
Space / P          pause/resume (also restarts playback after Stop)
Left  / Right      seek -10 / +10 seconds
H / L              same as Left/Right
Up / Down          listener A volume ±5%
Shift+Up/Down      listener B volume ±5%
M                  mute/unmute both listeners (toggle)
F9                 toggle playlist sidebar
F11                toggle fullscreen
Escape             exit fullscreen
AudioNext / Prev   next / previous queue entry
```

Shortcuts are ignored while a text entry or spin button has focus.

## Notes and current limitations

This is a prototype.

Current behaviour:

- Local files only.
- Routes audio tracks by stream ID when available, falling back to decoded pad order.
- Routes subtitles by stream ID when available; in-place subtitle switching avoids pipeline rebuilds when the target track has been seen at least once.
- Best tested with MKV files containing two audio tracks.
- Uses `pulsesink`, so it expects PulseAudio or PipeWire-Pulse.
- Subtitle rendering depends on the installed GStreamer subtitle plugins and the subtitle format.
- Includes both CLI playback and a GTK UI.

Possible future improvements:

1. Microphone-loop calibration to measure and store per-sink delays automatically.
2. N-listener support (more than two simultaneous outputs).
3. External `subparse` for MKV so subtitle track switches never need to wait for the next cue.

## Simple synthetic test file idea

Create or obtain an MKV with two obvious audio tracks and at least one subtitle track, such as:

```text
Track 0: left listener hears a tone or spoken "Japanese track"
Track 1: right listener hears a different tone or spoken "English track"
Subtitle 0: visible test subtitle text
```

Then use `pavucontrol` while playing to confirm each stream is routed to the expected output.

## Troubleshooting mixed audio codecs

If one track plays and the other does not, check the runtime output from the player.
This version prints every linked pad and its caps, for example:

```text
Linked Listener A track 0: audio/x-raw, ...
Linked Listener B track 1: audio/x-raw, ...
```

For files like:

```text
audio #2: AAC      -> audioTrack 0
audio #3: E-AC-3  -> audioTrack 1
```

make sure the E-AC-3 decoder is available:

```bash
gst-inspect-1.0 avdec_eac3
```

If that returns nothing, install/reinstall:

```bash
sudo apt install gstreamer1.0-libav gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
```

If the player says a selected listener track was not linked after startup, it usually means GStreamer did not expose that track as decoded audio. That is normally a missing decoder/plugin issue or a stream-selection issue we need to handle more explicitly in the next version.

## Troubleshooting subtitles

This version renders decoded raw text subtitles with GStreamer's `textoverlay` element. Decoded `text/x-raw` subtitle streams in either `pango-markup` or `utf8` format are routed through an `input-selector` and a raw-text capsfilter before they reach the overlay.

Subtitle track switching is scheduled asynchronously on the selector so the GTK UI is not blocked by GStreamer stream locks. On sparse subtitle streams, the newly selected track may not display text until its next cue.

If ASS/SSA subtitles still fail, test with a simpler subtitle track first, such as SRT or Timed Text. ASS subtitle rendering can depend on which subtitle plugins are installed.

## Keyboard control note

Keyboard controls are read from the terminal, not from the video window. Keep the terminal focused while testing controls.

Controls:

```text
space / p      pause/resume
q              quit
left / right   seek -10/+10 seconds
h / l          seek -10/+10 seconds fallback keys
```

If the video window has focus, keypresses may be consumed by the video sink/window manager and the script will not receive them.

GTK fullscreen controls:

```text
F11                  toggle fullscreen
Escape              exit fullscreen
double-click video  toggle fullscreen
right-click video   open fullscreen menu
```
