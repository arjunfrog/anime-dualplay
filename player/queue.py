from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from player.models import MediaInfo


@dataclass
class QueueEntry:
    media_path: Path
    media_info: Optional[MediaInfo] = None
    status: str = "pending"

    def to_dict(self) -> dict:
        return {
            "path": str(self.media_path),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> QueueEntry:
        return cls(
            media_path=Path(data["path"]).resolve(),
            media_info=None,
            status=data.get("status", "pending"),
        )


@dataclass
class QueueModel:
    entries: list[QueueEntry] = field(default_factory=list)
    current_index: int = -1

    @property
    def current(self) -> Optional[QueueEntry]:
        if 0 <= self.current_index < len(self.entries):
            return self.entries[self.current_index]
        return None

    @property
    def empty(self) -> bool:
        return len(self.entries) == 0

    @property
    def has_next(self) -> bool:
        return self.current_index + 1 < len(self.entries)

    @property
    def has_previous(self) -> bool:
        return self.current_index > 0

    @property
    def is_at_end(self) -> bool:
        return self.current_index >= len(self.entries) - 1

    @property
    def is_at_beginning(self) -> bool:
        return self.current_index <= 0

    def add(self, path: Path) -> None:
        self.entries.append(QueueEntry(media_path=path))

    def add_entries(self, paths: list[Path]) -> None:
        for p in paths:
            self.entries.append(QueueEntry(media_path=p))

    def remove(self, index: int) -> Optional[QueueEntry]:
        if index < 0 or index >= len(self.entries):
            return None

        removed = self.entries.pop(index)

        if index < self.current_index:
            self.current_index -= 1
        elif index == self.current_index:
            if len(self.entries) == 0:
                self.current_index = -1
            elif self.current_index >= len(self.entries):
                self.current_index = len(self.entries) - 1

        return removed

    def move(self, from_idx: int, to_idx: int) -> None:
        if from_idx < 0 or from_idx >= len(self.entries):
            return
        if to_idx < 0:
            to_idx = 0
        if to_idx >= len(self.entries):
            to_idx = len(self.entries) - 1
        if from_idx == to_idx:
            return

        moving_current = self.current_index == from_idx
        entry = self.entries.pop(from_idx)

        if from_idx < self.current_index:
            self.current_index -= 1

        self.entries.insert(to_idx, entry)

        if moving_current:
            self.current_index = to_idx
        elif to_idx <= self.current_index:
            self.current_index += 1

    def clear(self) -> None:
        self.entries.clear()
        self.current_index = -1

    def advance(self) -> bool:
        if self.has_next:
            self.current_index += 1
            return True
        return False

    def retreat(self) -> bool:
        if self.has_previous:
            self.current_index -= 1
            return True
        return False

    def jump_to(self, index: int) -> bool:
        if 0 <= index < len(self.entries):
            self.current_index = index
            return True
        return False

    def to_dict(self) -> list[dict]:
        return [entry.to_dict() for entry in self.entries]

    @classmethod
    def from_dict_list(cls, data: list[dict], current_index: int = -1) -> QueueModel:
        entries = [QueueEntry.from_dict(d) for d in data]
        if current_index >= len(entries):
            current_index = -1
        return cls(entries=entries, current_index=current_index)
