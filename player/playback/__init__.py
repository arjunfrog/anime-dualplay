"""Playback subpackage. Splits the former monolithic player.engine into:

- bt_classifier   pure helper for Bluetooth error attribution
- pipeline_builder constructs the GStreamer pipeline and per-stream branches
- pad_router      handles decodebin pad-added dispatch and per-pipeline state
- session         lifecycle, bus, position timer, live setters

The legacy player.engine module re-exports PlaybackSession as PlaybackEngine
for backward compatibility.
"""

from player.playback.session import PlaybackSession, PlaybackObserver

__all__ = ["PlaybackSession", "PlaybackObserver"]
