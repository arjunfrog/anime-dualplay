# Remediation Audit

| # | Finding | Status | Remediation |
|---|---|---|---|
| 1 | Multiple playback pipelines can run at once | Fixed | GTK cancels pending retries and destroys any existing engine before starting another. |
| 2 | Live route changes break GUI rebuilds | Fixed | Rebuild preserves the prepared video sink and restores playback position/state. |
| 3 | Stop/EOS/error do not release pipeline | Fixed | Stop and destroy transition the pipeline to `Gst.State.NULL`. |
| 4 | Rebuild position preservation unreliable | Fixed | Rebuild pauses the new pipeline, seeks, then restores target state and position timer. |
| 5 | Routing depends on decoded pad order | Mitigated | Playback maps pads by discovered stream ID and warns before pad-order fallback. |
| 6 | Same audio track cannot route to both listeners | Fixed | Duplicate selections use an audio `tee` with independent listener branches. |
| 7 | No preflight validation | Fixed | CLI and GTK validate media tracks, sinks, and subtitle/video compatibility before playback. |
| 8 | Device disconnect handling weak | Fixed | GStreamer errors release playback and GTK refreshes sinks with recovery guidance. |
| 9 | Latency compensation static | Mitigated | Engine recalculates latency on bus messages and documents manual delay calibration. |
| 10 | No explicit channel mapping policy | Fixed | Listener branches normalize to stereo PCM; channel-specific routing remains future scope. |
| 11 | Bus watches not removed | Fixed | Bus handler IDs and signal watches are tracked and removed. |
| 12 | Device discovery can block/leak | Fixed | Device monitor stop runs in `finally`; GTK refresh runs on a background thread. |
| 13 | Stale startup timers | Fixed | Startup timer IDs and generation tokens prevent stale status reports. |

No database migrations were required.
