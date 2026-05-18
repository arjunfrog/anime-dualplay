# Configuration

Configuration is stored in `config.json`. The current format supports named profiles:

```json
{
  "version": 1,
  "active_profile": "default",
  "profiles": {
    "default": {
      "listenerA": {"label": "Listener A", "sink": "...", "audioTrack": 0, "delayMs": 0, "volume": 1.0},
      "listenerB": {"label": "Listener B", "sink": "...", "audioTrack": 1, "delayMs": 0, "volume": 1.0},
      "video": {"enabled": true, "sink": "autovideosink"},
      "subtitles": {"enabled": false, "subtitleTrack": 0}
    }
  }
}
```

## Validation Rules

- `sink` must be a non-empty PulseAudio or PipeWire-Pulse sink name.
- At least one audio output sink must be discoverable before playback starts.
- `audioTrack` and `subtitleTrack` are zero-based indexes within each stream type.
- `volume` is clamped to `0.0` through `2.0`.
- `delayMs` is clamped to `0` through `2000`.
- Subtitles require video output.
- Both listeners may select the same audio track; playback fans the decoded stream out to both sinks.

Legacy non-profile configs are still accepted and converted to `PlayerConfig` in memory.
