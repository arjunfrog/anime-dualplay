from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

from player.queue import QueueEntry, QueueModel


@dataclass
class QueueObserver:
    on_transition: Optional[Callable[[Path, int], None]] = None
    on_stop: Optional[Callable[[], None]] = None
    on_status: Optional[Callable[[str, bool], None]] = None
    on_queue_changed: Optional[Callable[[], None]] = None


class QueueController:
    """Orchestrates queue transitions and bridges QueueModel <-> PlaybackEngine.

    Owns the QueueModel data. Communicates with the UI via callbacks.
    Does NOT directly import PlaybackEngine or GTK.
    """

    def __init__(self):
        self._queue = QueueModel()
        self._transitioning = False
        self._pending_skip_forward = 0
        self._pending_skip_backward = 0
        self._consecutive_errors = 0
        self._max_consecutive_errors = 3

        self._on_transition_requested: Optional[Callable[[Path, int], None]] = None
        self._on_stop_requested: Optional[Callable[[], None]] = None
        self._on_status: Optional[Callable[[str, bool], None]] = None
        self._on_queue_changed: Optional[Callable[[], None]] = None

    @property
    def queue(self) -> QueueModel:
        return self._queue

    def set_transition_callback(self, cb: Optional[Callable[[Path, int], None]]) -> None:
        self._on_transition_requested = cb

    def set_stop_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_stop_requested = cb

    def set_status_callback(self, cb: Optional[Callable[[str, bool], None]]) -> None:
        self._on_status = cb

    def set_queue_changed_callback(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_queue_changed = cb

    def set_observer(self, observer: QueueObserver) -> None:
        self._on_transition_requested = observer.on_transition
        self._on_stop_requested = observer.on_stop
        self._on_status = observer.on_status
        self._on_queue_changed = observer.on_queue_changed

    def _emit_status(self, message: str, is_error: bool = False) -> None:
        if self._on_status:
            self._on_status(message, is_error)

    def _emit_queue_changed(self) -> None:
        if self._on_queue_changed:
            self._on_queue_changed()

    def load_from_data(self, data: list[dict], index: int) -> None:
        self._queue = QueueModel.from_dict_list(data, current_index=index)
        self._emit_queue_changed()

    def to_data(self) -> tuple[list[dict], int]:
        return (self._queue.to_dict(), self._queue.current_index)

    def add_files(self, paths: list[Path]) -> None:
        if not paths:
            return
        was_empty = self._queue.empty
        self._queue.add_entries(paths)
        self._emit_queue_changed()
        if was_empty:
            self.play_entry(0)

    def remove_entry(self, index: int) -> None:
        was_playing = index == self._queue.current_index
        self._queue.remove(index)
        self._emit_queue_changed()
        if was_playing:
            if self._queue.current is not None:
                self._play_entry_at_index(self._queue.current_index)
            else:
                self._request_stop()

    def move_entry(self, from_idx: int, to_idx: int) -> None:
        self._queue.move(from_idx, to_idx)
        self._emit_queue_changed()

    def clear_queue(self) -> None:
        self._queue.clear()
        self._consecutive_errors = 0
        self._request_stop()
        self._emit_queue_changed()
        self._emit_status("Queue cleared", is_error=False)

    def play_entry(self, index: int) -> bool:
        if not self._queue.jump_to(index):
            self._emit_status("Invalid queue index", is_error=True)
            return False
        self._play_entry_at_index(index)
        return True

    def _play_entry_at_index(self, index: int) -> None:
        entry = self._queue.current
        if entry is None:
            return
        entry.status = "playing"
        self._emit_queue_changed()
        if self._on_transition_requested:
            self._on_transition_requested(entry.media_path, index)

    def skip_forward(self) -> None:
        if self._queue.empty:
            self._emit_status("Queue is empty", is_error=True)
            return

        if self._queue.is_at_end:
            self._emit_status("End of queue reached", is_error=False)
            return

        if self._transitioning:
            self._pending_skip_forward += 1
            self._pending_skip_backward = 0
            return

        self._execute_skip_forward()

    def _execute_skip_forward(self) -> None:
        if self._queue.current_index >= 0 and self._queue.current:
            self._queue.current.status = "played"

        target = self._queue.current_index + 1
        extra = self._pending_skip_forward
        self._pending_skip_forward = 0
        target += extra

        if target >= len(self._queue.entries):
            target = len(self._queue.entries) - 1

        if target == self._queue.current_index:
            self._emit_status("End of queue reached", is_error=False)
            return

        self._begin_transition()
        self._queue.current_index = target
        self._play_entry_at_index(target)

    def skip_backward(self) -> None:
        if self._queue.empty:
            self._emit_status("Queue is empty", is_error=True)
            return

        if self._queue.is_at_beginning:
            self._emit_status("Already at the beginning of the queue", is_error=False)
            return

        if self._transitioning:
            self._pending_skip_backward += 1
            self._pending_skip_forward = 0
            return

        self._execute_skip_backward()

    def _execute_skip_backward(self) -> None:
        if self._queue.current_index >= 0 and self._queue.current:
            self._queue.current.status = "pending"

        target = self._queue.current_index - 1
        extra = self._pending_skip_backward
        self._pending_skip_backward = 0
        target -= extra

        if target < 0:
            target = 0

        if target == self._queue.current_index:
            self._emit_status("Already at the beginning of the queue", is_error=False)
            return

        self._begin_transition()
        self._queue.current_index = target
        self._play_entry_at_index(target)

    def on_eos(self) -> None:
        """Called by the UI when the engine reports end-of-stream."""
        if self._transitioning:
            return

        if self._queue.current:
            self._queue.current.status = "played"

        if self._queue.has_next:
            self._begin_transition()
            self._queue.advance()
            self._play_entry_at_index(self._queue.current_index)
        else:
            self._emit_status("End of queue", is_error=False)
            self._request_stop()

    def on_error(self, _message: str) -> None:
        if self._queue.current:
            self._queue.current.status = "error"
            self._emit_queue_changed()

        self._consecutive_errors += 1

        if self._transitioning:
            return

        if self._consecutive_errors > self._max_consecutive_errors:
            self._emit_status(
                f"Too many consecutive errors ({self._max_consecutive_errors}). Stopping queue.",
                is_error=True,
            )
            self._consecutive_errors = 0
            self._request_stop()
            return

        if self._queue.has_next:
            self._begin_transition()
            self._queue.advance()
            self._play_entry_at_index(self._queue.current_index)
        else:
            self._consecutive_errors = 0
            self._emit_status("All queue entries failed to load", is_error=True)
            self._request_stop()

    def on_transition_complete(self) -> None:
        """Called by the UI when a transition has finished (new entry is playing)."""
        self._transitioning = False
        self._consecutive_errors = 0

        if self._pending_skip_forward > 0:
            self._execute_skip_forward()
        elif self._pending_skip_backward > 0:
            self._execute_skip_backward()

    def _begin_transition(self) -> None:
        self._transitioning = True

    def _request_stop(self) -> None:
        if self._on_stop_requested:
            self._on_stop_requested()

    def is_transitioning(self) -> bool:
        return self._transitioning
