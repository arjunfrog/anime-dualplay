# Steering Guidelines

## Playback Invariants

- A playback pipeline must have one owner and must be released with `Gst.State.NULL` on stop, EOS, error, rebuild, and window close.
- Bus watches, GLib timers, and GTK retry timers must be stored and removed when no longer valid.
- GUI rebuilds must preserve the external video sink before destroying the old pipeline.
- Stale callbacks must not report status for an old pipeline generation.

## Routing And Sync

- Prefer discovered stream IDs for track routing. Fall back to decoded pad order only with a visible warning.
- If both listeners select the same audio track, fan out one decoded stream with a `tee`; do not leave one listener disconnected.
- Listener branches normalize output to stereo PCM. Channel-specific routing is out of scope until the config schema explicitly supports it.
- Positive delay uses sink `render-delay`; negative delay is unsupported and must be clamped or represented by delaying the other branch.

## Robustness

- Validate media tracks and sink names before building a pipeline.
- Device discovery must not block the GTK main thread.
- Device errors should release the pipeline, refresh available sinks, and guide the user to choose a valid output.

## Documentation

- Keep `README.md` user-facing and link deeper docs from `docs/`.
- Record each meaningful change in `DEVLOG.md`, including tests run and whether migrations were needed.
