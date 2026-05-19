from __future__ import annotations

from pathlib import Path
import pytest

from player.queue import QueueModel
from player.queue_controller import QueueController


@pytest.fixture
def controller():
    ctrl = QueueController()
    ctrl._on_transition_requested = None
    ctrl._on_stop_requested = None
    ctrl._on_status = None
    ctrl._on_queue_changed = None
    return ctrl


@pytest.fixture
def wired_controller():
    ctrl = QueueController()
    events = {
        "transitions": [],
        "stops": 0,
        "statuses": [],
        "queue_changes": 0,
    }

    def on_transition(path, index):
        events["transitions"].append((str(path), index))

    def on_stop():
        events["stops"] += 1

    def on_status(msg, is_error):
        events["statuses"].append((msg, is_error))

    def on_queue_changed():
        events["queue_changes"] += 1

    ctrl.set_transition_callback(on_transition)
    ctrl.set_stop_callback(on_stop)
    ctrl.set_status_callback(on_status)
    ctrl.set_queue_changed_callback(on_queue_changed)

    return ctrl, events


class TestQueueControllerInit:
    def test_initial_state_empty(self, controller):
        q = controller.queue
        assert q.empty
        assert q.current is None
        assert controller.is_transitioning() is False


class TestAddFiles:
    def test_add_files_populates_queue(self, controller):
        paths = [Path("/tmp/a.mkv"), Path("/tmp/b.mkv")]
        controller.add_files(paths)
        assert len(controller.queue.entries) == 2

    def test_add_files_empty_queue_auto_plays(self, wired_controller):
        ctrl, events = wired_controller
        paths = [Path("/tmp/a.mkv")]
        ctrl.add_files(paths)
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/a.mkv"
        assert events["transitions"][0][1] == 0

    def test_add_files_non_empty_does_not_auto_play(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv")])
        events["transitions"].clear()
        ctrl.add_files([Path("/tmp/b.mkv")])
        assert len(events["transitions"]) == 0

    def test_add_files_empty_list_noop(self, controller):
        controller.add_files([])
        assert controller.queue.empty


class TestRemoveEntry:
    def test_remove_non_playing(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        events["transitions"].clear()
        ctrl.remove_entry(1)
        assert len(ctrl.queue.entries) == 1
        assert len(events["transitions"]) == 0

    def test_remove_playing_advances(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        events["transitions"].clear()
        ctrl.remove_entry(0)
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/b.mkv"

    def test_remove_last_playing_stops(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv")])
        events["transitions"].clear()
        ctrl.remove_entry(0)
        assert events["stops"] == 1


class TestPlayEntry:
    def test_play_valid_index(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        events["transitions"].clear()
        ctrl.play_entry(1)
        assert events["transitions"][0][1] == 1

    def test_play_invalid_index(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv")])
        events["statuses"].clear()
        result = ctrl.play_entry(5)
        assert result is False
        statuses = [s for s in events["statuses"] if s[1]]
        assert any("Invalid queue index" in s[0] for s in statuses)


class TestSkipForward:
    def test_skip_forward_empty_queue(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.skip_forward()
        assert any("Queue is empty" in s[0] for s in events["statuses"])

    def test_skip_forward_single_entry_at_end(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.skip_forward()
        assert any("End of queue" in s[0] for s in events["statuses"])

    def test_skip_forward_mid_queue(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv"), Path("/tmp/c.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        events["transitions"].clear()
        ctrl.skip_forward()
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/b.mkv"

    def test_skip_forward_marks_played(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.skip_forward()
        assert ctrl.queue.entries[0].status == "played"


class TestSkipBackward:
    def test_skip_backward_empty_queue(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.skip_backward()
        assert any("Queue is empty" in s[0] for s in events["statuses"])

    def test_skip_backward_at_beginning(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.skip_backward()
        assert any("beginning" in s[0] for s in events["statuses"])

    def test_skip_backward_mid_queue(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv"), Path("/tmp/c.mkv")])
        ctrl.queue.jump_to(1)
        ctrl.on_transition_complete()
        events["transitions"].clear()
        ctrl.skip_backward()
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/a.mkv"

    def test_skip_backward_marks_pending(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(1)
        ctrl.on_transition_complete()
        ctrl.skip_backward()
        assert ctrl.queue.entries[1].status == "pending"


class TestDebounce:
    def test_skip_forward_during_transition_coalesces(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv"), Path("/tmp/c.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()

        ctrl._begin_transition()
        ctrl.skip_forward()
        ctrl.skip_forward()

        assert ctrl._pending_skip_forward == 2
        assert ctrl._pending_skip_backward == 0

    def test_pending_skips_execute_on_transition_complete(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv"), Path("/tmp/c.mkv"), Path("/tmp/d.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        events["transitions"].clear()

        ctrl._begin_transition()
        ctrl.skip_forward()
        ctrl.skip_forward()

        assert ctrl._pending_skip_forward == 2
        ctrl.on_transition_complete()
        assert ctrl._pending_skip_forward == 0
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/d.mkv"

    def test_skip_backward_during_transition(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv"), Path("/tmp/c.mkv")])
        ctrl.queue.jump_to(2)
        ctrl.on_transition_complete()

        ctrl._begin_transition()
        ctrl.skip_backward()
        ctrl.skip_backward()

        assert ctrl._pending_skip_backward == 2
        ctrl.on_transition_complete()
        assert ctrl.queue.current_index == 0


class TestEOS:
    def test_on_eos_advances(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        events["transitions"].clear()
        ctrl.on_eos()
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/b.mkv"

    def test_on_eos_last_entry_stops(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.on_eos()
        assert events["stops"] == 1
        assert ctrl.queue.entries[0].status == "played"

    def test_on_eos_during_transition_does_nothing(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl._begin_transition()
        events["transitions"].clear()
        ctrl.on_eos()
        assert len(events["transitions"]) == 0

    def test_on_eos_marks_played(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.on_eos()
        assert ctrl.queue.entries[0].status == "played"


class TestOnError:
    def test_on_error_advances_to_next(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        events["transitions"].clear()
        ctrl.on_error("test error")
        assert len(events["transitions"]) == 1
        assert events["transitions"][0][0] == "/tmp/b.mkv"
        assert ctrl.queue.entries[0].status == "error"

    def test_on_error_consecutive_guard(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv"),
                        Path("/tmp/c.mkv"), Path("/tmp/d.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()

        ctrl._consecutive_errors = 3
        events["statuses"].clear()
        ctrl.on_error("err")
        assert any("Too many consecutive errors" in s[0] for s in events["statuses"])
        assert events["stops"] >= 1

    def test_on_error_last_entry_stops(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.on_error("err")
        assert events["stops"] == 1
        assert any("All queue entries failed" in s[0] for s in events["statuses"])


class TestClearQueue:
    def test_clear_stops_and_emits(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        ctrl.on_transition_complete()
        ctrl.clear_queue()
        assert ctrl.queue.empty
        assert events["stops"] >= 1
        assert any("Queue cleared" in s[0] for s in events["statuses"])


class TestMoveEntry:
    def test_move_does_not_trigger_transition(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        events["transitions"].clear()
        ctrl.move_entry(0, 1)
        assert len(events["transitions"]) == 0


class TestTransitionComplete:
    def test_resets_consecutive_errors(self, wired_controller):
        ctrl, events = wired_controller
        ctrl._consecutive_errors = 5
        ctrl.on_transition_complete()
        assert ctrl._consecutive_errors == 0

    def test_resets_transitioning(self, wired_controller):
        ctrl, events = wired_controller
        ctrl._begin_transition()
        assert ctrl.is_transitioning()
        ctrl.on_transition_complete()
        assert not ctrl.is_transitioning()


class TestLoadFromData:
    def test_restores_queue(self, wired_controller):
        ctrl, events = wired_controller
        data = [
            {"path": "/tmp/a.mkv", "status": "played"},
            {"path": "/tmp/b.mkv", "status": "pending"},
        ]
        ctrl.load_from_data(data, 0)
        assert len(ctrl.queue.entries) == 2
        assert ctrl.queue.current_index == 0
        assert ctrl.queue.current.media_path == Path("/tmp/a.mkv").resolve()

    def test_to_data_roundtrip(self, wired_controller):
        ctrl, events = wired_controller
        ctrl.add_files([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        ctrl.queue.jump_to(0)
        queue_data, index = ctrl.to_data()
        assert len(queue_data) == 2
        assert queue_data[0]["path"] == "/tmp/a.mkv"
        assert index == 0
