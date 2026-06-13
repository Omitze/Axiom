"""JSON-based persistence for the Memory module.

Stores memory items as individual JSON files under
``~/.axiom/memory/<id>.json`` for easy inspection and debugging.
Also provides a bulk export/import mechanism."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .models import MemoryItem


class MemoryStorage:
    """Persist and load MemoryItems to/from the filesystem.

    Each item is stored as a separate JSON file keyed by its ``id``.
    This avoids loading all items on every operation and makes manual
    inspection trivial.
    """

    def __init__(self, dir_path: str | Path | None = None):
        self.dir_path = Path(dir_path or Path.home() / ".axiom" / "memory")
        self.dir_path.mkdir(parents=True, exist_ok=True)

    # -- single item ops -----------------------------------------------------

    def _item_path(self, item_id: str) -> Path:
        return (self.dir_path / f"{item_id}.json").resolve()

    def save_item(self, item: MemoryItem) -> None:
        """Write a single MemoryItem to disk."""
        path = self._item_path(item.id)
        path.write_text(
            json.dumps(item.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_item(self, item_id: str) -> MemoryItem | None:
        """Load a single item by id. Returns ``None`` if not found."""
        path = self._item_path(item_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MemoryItem.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def delete_item(self, item_id: str) -> bool:
        """Delete a single item by id. Returns ``True`` if it existed."""
        path = self._item_path(item_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def exists(self, item_id: str) -> bool:
        """Check if an item exists on disk."""
        return self._item_path(item_id).exists()

    # -- bulk ops ------------------------------------------------------------

    def save_all(self, items: list[MemoryItem]) -> None:
        """Write every item in the list (replaces all files)."""
        # First clear existing files to handle deletions
        self.clear()
        for item in items:
            self.save_item(item)

    def load_all(self) -> list[MemoryItem]:
        """Load every memory item from the storage directory."""
        items: list[MemoryItem] = []
        for path in sorted(self.dir_path.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append(MemoryItem.from_dict(data))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return items

    def count(self) -> int:
        """Number of JSON files in the storage directory."""
        return len(list(self.dir_path.glob("*.json")))

    def clear(self) -> None:
        """Remove all JSON files from the storage directory."""
        for path in self.dir_path.glob("*.json"):
            path.unlink()

    # -- export / import -----------------------------------------------------

    def export_json(self, path: str | Path) -> None:
        """Export all memories as a single JSON array file."""
        items = self.load_all()
        data = [it.to_dict() for it in items]
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def import_json(self, path: str | Path) -> int:
        """Import memories from a JSON array file. Returns number imported."""
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array")

        count = 0
        for entry in data:
            try:
                item = MemoryItem.from_dict(entry)
                self.save_item(item)
                count += 1
            except (KeyError, ValueError):
                continue
        return count

    @property
    def path(self) -> Path:
        return self.dir_path
