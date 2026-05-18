# Playback Sync

The app uses a single GStreamer pipeline so video, subtitles, and listener audio branches share one playback clock.

## Delay Compensation

Each listener can apply a positive sink `render-delay` using `delayMs`. Negative delay is not supported because a sink cannot render before the pipeline clock. To compensate for a late device, delay the other listener instead.

## Latency Handling

The engine responds to GStreamer `LATENCY` bus messages by recalculating pipeline latency. This helps when sinks report changed buffering, but it is not automatic long-term hardware drift correction.

## Output Policy

Listener branches normalize audio to stereo PCM before volume and sink output. This keeps routing predictable for stereo headphones and DACs. Channel-specific routing requires a future config schema.
