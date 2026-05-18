# Architecture

Dual Audio Player has two entrypoints:

- `dual_audio_player.py`: CLI playback and media inspection.
- `app.py`: GTK application with embedded video, profile controls, routing controls, subtitles, and playback controls.

## Pipeline Shape

The engine builds one GStreamer pipeline per playback session:

```text
filesrc -> decodebin
  audio pad -> queue -> audioconvert -> audioresample -> stereo caps -> volume -> pulsesink
  audio pad selected by both listeners -> tee -> independent listener branches
  video pad -> queue -> videoconvert -> [textoverlay] -> video sink
  subtitle pad -> queue -> input-selector -> raw text caps -> textoverlay
  unused pads -> queue -> fakesink
```

One pipeline keeps video, subtitles, and audio branches on a shared playback clock.

## Lifecycle

`PlaybackEngine` owns the pipeline, bus signal watch, startup timer, and GUI position timer. Stop, EOS, errors, rebuilds, and window close must release the pipeline to `Gst.State.NULL`.

GUI route changes prepare a fresh video sink, rebuild the pipeline, seek back to the previous position, and restore the old play/pause state. Subtitle track changes use the existing selector and schedule the active-pad switch asynchronously so the GTK callback does not block on GStreamer stream locks.
