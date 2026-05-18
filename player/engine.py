"""Backward-compatible facade for the playback subpackage.

The original player.engine module was split into player.playback.* in
the engine refactor. This shim keeps existing import sites working:

    from player.engine import PlaybackEngine, PlaybackObserver

Both names resolve to the new home in player.playback.session.
PlaybackEngine is an alias of PlaybackSession.
"""

from __future__ import annotations

from player.playback.bt_classifier import _BT_SPECIFIC_KEYWORDS  # re-exported for tests
from player.playback.session import PlaybackObserver, PlaybackSession

# Public alias preserving the historical class name.
PlaybackEngine = PlaybackSession

__all__ = ["PlaybackEngine", "PlaybackObserver", "_BT_SPECIFIC_KEYWORDS"]
