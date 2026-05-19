from __future__ import annotations

from pathlib import Path
import pytest

from player.queue import QueueEntry, QueueModel


class TestQueueEntry:
    def test_serialize_deserialize(self):
        entry = QueueEntry(media_path=Path("/tmp/test.mkv"), status="pending")
        d = entry.to_dict()
        assert d["path"] == "/tmp/test.mkv"
        assert d["status"] == "pending"

        restored = QueueEntry.from_dict(d)
        assert restored.media_path == Path("/tmp/test.mkv").resolve()
        assert restored.status == "pending"
        assert restored.media_info is None

    def test_from_dict_missing_status(self):
        restored = QueueEntry.from_dict({"path": "/tmp/test.mkv"})
        assert restored.status == "pending"


class TestQueueModel:
    def test_empty_queue(self):
        q = QueueModel()
        assert q.empty
        assert q.current is None
        assert not q.has_next
        assert not q.has_previous
        assert q.is_at_end
        assert q.is_at_beginning

    def test_add_and_current(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        assert len(q.entries) == 2
        assert q.current is None
        assert q.current_index == -1

    def test_add_entries(self):
        q = QueueModel()
        q.add_entries([Path("/tmp/a.mkv"), Path("/tmp/b.mkv")])
        assert len(q.entries) == 2

    def test_advance_retreat(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))
        q.current_index = 0  # playing first entry

        assert q.advance()
        assert q.current_index == 1
        assert q.current.media_path == Path("/tmp/b.mkv")

        assert q.advance()
        assert q.current_index == 2
        assert q.current.media_path == Path("/tmp/c.mkv")

        assert not q.advance()
        assert q.current_index == 2
        assert q.is_at_end

        assert q.retreat()
        assert q.current_index == 1
        assert q.current.media_path == Path("/tmp/b.mkv")

        assert q.retreat()
        assert q.current_index == 0

        assert not q.retreat()
        assert q.current_index == 0
        assert q.is_at_beginning

    def test_jump_to(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))

        assert q.jump_to(2)
        assert q.current_index == 2
        assert q.current.media_path == Path("/tmp/c.mkv")

        assert not q.jump_to(5)
        assert q.current_index == 2

        assert not q.jump_to(-2)
        assert q.current_index == 2

    def test_remove_before_current(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))
        q.current_index = 2  # at c

        q.remove(0)  # remove a
        assert q.current_index == 1  # shifted
        assert q.current.media_path == Path("/tmp/c.mkv")

    def test_remove_after_current(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))
        q.current_index = 0  # at a

        q.remove(2)  # remove c
        assert q.current_index == 0  # unchanged
        assert q.current.media_path == Path("/tmp/a.mkv")

    def test_remove_current(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))
        q.current_index = 1  # at b

        q.remove(1)  # remove b
        assert q.current_index == 1  # stays at position (now c)
        assert q.current.media_path == Path("/tmp/c.mkv")

    def test_remove_current_last(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.current_index = 0

        q.remove(0)
        assert q.empty
        assert q.current_index == -1
        assert q.current is None

    def test_remove_current_from_end(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.current_index = 1  # at b, last element

        q.remove(1)
        assert q.current_index == 0  # shifts to a
        assert q.current.media_path == Path("/tmp/a.mkv")

    def test_remove_invalid_index(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.current_index = 0

        result = q.remove(5)
        assert result is None
        assert len(q.entries) == 1
        assert q.current_index == 0

    def test_move_basic(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))
        q.current_index = 0

        q.move(2, 0)  # move c to top
        assert q.entries[0].media_path == Path("/tmp/c.mkv")
        assert q.entries[1].media_path == Path("/tmp/a.mkv")
        assert q.entries[2].media_path == Path("/tmp/b.mkv")
        assert q.current_index == 1  # "a" shifted to index 1

    def test_move_current_entry(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.add(Path("/tmp/c.mkv"))
        q.current_index = 1  # at b

        q.move(1, 2)  # move b down
        assert q.current_index == 2  # follows the entry

    def test_move_same_position(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.current_index = 0

        q.move(0, 0)  # no-op
        assert q.current_index == 0

    def test_clear(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.current_index = 1

        q.clear()
        assert q.empty
        assert q.current_index == -1

    def test_serialize_deserialize(self):
        q = QueueModel()
        q.add(Path("/tmp/a.mkv"))
        q.add(Path("/tmp/b.mkv"))
        q.entries[0].status = "played"
        q.entries[1].status = "pending"
        q.current_index = 0

        data = q.to_dict()
        restored = QueueModel.from_dict_list(data, current_index=0)
        assert len(restored.entries) == 2
        assert restored.entries[0].media_path == Path("/tmp/a.mkv").resolve()
        assert restored.entries[0].status == "played"
        assert restored.entries[1].media_path == Path("/tmp/b.mkv").resolve()
        assert restored.entries[1].status == "pending"
        assert restored.current_index == 0
        assert restored.current.media_path == Path("/tmp/a.mkv").resolve()

    def test_has_next_has_previous_edge_cases(self):
        q = QueueModel()
        assert not q.has_next
        assert not q.has_previous

        q.add(Path("/tmp/a.mkv"))
        q.current_index = 0
        assert not q.has_next
        assert not q.has_previous

        q.add(Path("/tmp/b.mkv"))
        # current_index 0, 2 entries
        assert q.has_next
        assert not q.has_previous

        q.advance()
        assert not q.has_next
        assert q.has_previous

    def test_move_from_before_current_to_after(self):
        q = QueueModel()
        for name in ("a", "b", "c", "d", "e"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 3  # at d
        q.move(0, 4)  # move a to position 4 (end)
        assert q.entries[0].media_path == Path("/tmp/b.mkv")
        assert q.entries[1].media_path == Path("/tmp/c.mkv")
        assert q.entries[2].media_path == Path("/tmp/d.mkv")
        assert q.entries[3].media_path == Path("/tmp/e.mkv")
        assert q.entries[4].media_path == Path("/tmp/a.mkv")
        assert q.current_index == 2  # d shifted from 3 to 2

    def test_move_from_after_current_to_before(self):
        q = QueueModel()
        for name in ("a", "b", "c", "d", "e"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 1  # at b
        q.move(3, 0)  # move d to position 0
        assert q.entries[0].media_path == Path("/tmp/d.mkv")
        assert q.entries[1].media_path == Path("/tmp/a.mkv")
        assert q.entries[2].media_path == Path("/tmp/b.mkv")
        assert q.current_index == 2  # b shifted from 1 to 2

    def test_move_within_same_region_before_current(self):
        q = QueueModel()
        for name in ("a", "b", "c", "d"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 3  # at d
        q.move(0, 1)  # move a within before-region
        assert q.entries[0].media_path == Path("/tmp/b.mkv")
        assert q.entries[1].media_path == Path("/tmp/a.mkv")
        assert q.current_index == 3  # unchanged

    def test_move_within_same_region_after_current(self):
        q = QueueModel()
        for name in ("a", "b", "c", "d"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 0  # at a
        q.move(2, 3)  # move c within after-region
        assert q.entries[2].media_path == Path("/tmp/d.mkv")
        assert q.entries[3].media_path == Path("/tmp/c.mkv")
        assert q.current_index == 0  # unchanged

    def test_move_current_to_end(self):
        q = QueueModel()
        for name in ("a", "b", "c"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 0  # at a
        q.move(0, 2)  # move a to end
        assert q.entries[2].media_path == Path("/tmp/a.mkv")
        assert q.current_index == 2

    def test_move_current_to_front(self):
        q = QueueModel()
        for name in ("a", "b", "c"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 2  # at c
        q.move(2, 0)  # move c to front
        assert q.entries[0].media_path == Path("/tmp/c.mkv")
        assert q.current_index == 0

    def test_move_to_same_region_adjacent(self):
        q = QueueModel()
        for name in ("a", "b", "c"):
            q.add(Path(f"/tmp/{name}.mkv"))
        q.current_index = 1  # at b
        q.move(0, 2)  # move a from index 0 to index 2
        assert q.entries[0].media_path == Path("/tmp/b.mkv")
        assert q.entries[1].media_path == Path("/tmp/c.mkv")
        assert q.entries[2].media_path == Path("/tmp/a.mkv")
        assert q.current_index == 0  # b shifted from 1 to 0
