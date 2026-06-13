"""Memory manager — the main public API for the multi-layer memory system.

Provides ``remember``, ``recall``, ``get_recent``, ``forget``, and
``auto_forget`` operations backed by a persistent JSON store.
"""

from __future__ import annotations

import time
from pathlib import Path

from .models import MemoryItem, MemoryType
from .persistence import MemoryStorage
from .search import MemorySearch


class MemoryManager:
    """Multi-layer memory system with importance-based retention.

    Three memory layers:
    - **Episodic**  : raw conversation turns (what happened)
    - **Semantic**  : extracted decisions, patterns, facts (what was learned)
    - **Procedural** : reusable work patterns / mini-skills (how to do things)

    Usage::

        mem = MemoryManager()
        mem.remember("User asked about file paths", type="episodic", importance=0.3)
        mem.remember("Decision: use UUIDs for all IDs", type="semantic", importance=0.9)
        results = mem.recall("file paths")
        recent = mem.get_recent(n=5)
        mem.auto_forget(max_items=200)
    """

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        max_items: int = 500,
    ):
        self._storage = MemoryStorage(storage_dir)
        self._items: list[MemoryItem] = []
        self._max_items = max_items
        self._load()

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        type: str | MemoryType = MemoryType.EPISODIC,
        importance: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> MemoryItem:
        """Store a new memory item.

        Parameters
        ----------
        content:
            The memory content (text).
        type:
            One of ``"episodic"``, ``"semantic"``, ``"procedural"``
            (or the ``MemoryType`` enum value).
        importance:
            Retention priority between 0.0 (discard first) and 1.0 (keep at all costs).
        tags:
            Optional list of string tags for filtering.
        metadata:
            Optional arbitrary dict for extensibility.
        """
        item = MemoryItem(
            content=content,
            type=MemoryType(type) if isinstance(type, str) else type,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
        )
        self._items.append(item)
        self._storage.save_item(item)
        return item

    def recall(
        self,
        query: str,
        n: int = 5,
        types: MemoryType | list[MemoryType] | None = None,
    ) -> list[MemoryItem]:
        """Semantic / keyword search. Returns the top *n* relevant items.

        If *query* is empty, returns the most recent *n* items (filtered by *types*).

        .. code-block:: python

            results = mem.recall("file path decision", n=3)
            results = mem.recall("error pattern", types=MemoryType.SEMANTIC)
        """
        return MemorySearch.search(self._items, query, n=n, types=types)

    def get_recent(
        self,
        n: int = 10,
        types: MemoryType | list[MemoryType] | None = None,
    ) -> list[MemoryItem]:
        """Return the *n* most recently added memory items.

        Optionally filtered by *types*.
        """
        candidates = MemorySearch.by_type(self._items, types)
        sorted_items = sorted(candidates, key=lambda it: it.timestamp, reverse=True)
        return sorted_items[:n]

    def get_by_type(self, type: MemoryType | str) -> list[MemoryItem]:
        """Return all items of the given *type*."""
        return MemorySearch.by_type(self._items, type)

    def get_by_tag(self, tag: str) -> list[MemoryItem]:
        """Return all items that have the given *tag*."""
        return MemorySearch.by_tag(self._items, tag)

    def get_by_importance(
        self, min_imp: float = 0.0, max_imp: float = 1.0
    ) -> list[MemoryItem]:
        """Return items whose importance falls in the given range."""
        return MemorySearch.by_importance(self._items, min_imp, max_imp)

    def get(self, item_id: str) -> MemoryItem | None:
        """Look up a single item by its ``id``."""
        for it in self._items:
            if it.id == item_id:
                return it
        return None

    def forget(self, item_ids: list[str]) -> int:
        """Delete items by their IDs. Returns the number actually removed."""
        before = len(self._items)
        id_set = set(item_ids)
        self._items = [it for it in self._items if it.id not in id_set]
        removed = before - len(self._items)
        if removed:
            for item_id in item_ids:
                self._storage.delete_item(item_id)
        return removed

    def auto_forget(
        self, max_items: int | None = None, min_importance: float = 0.2
    ) -> int:
        """Trim low-importance memories when the total exceeds *max_items*.

        Strategy (same as zed.md spec):
        1. If total ≤ *max_items*, do nothing.
        2. Sort by importance ascending (lowest first).
        3. Remove items below *min_importance* that push us over the limit.
        4. If still over, remove the oldest low-importance items.

        Returns the number of items removed.
        """
        max_items = max_items or self._max_items
        if len(self._items) <= max_items:
            return 0

        to_remove = len(self._items) - max_items

        # Candidates: items with importance below the threshold, sorted (lowest first)
        candidates = sorted(
            [it for it in self._items if it.importance < min_importance],
            key=lambda it: (it.importance, it.timestamp),
        )

        removed_count = 0
        ids_to_remove: list[str] = []
        for item in candidates:
            if removed_count >= to_remove:
                break
            ids_to_remove.append(item.id)
            removed_count += 1

        # If still over the limit, remove oldest items (regardless of importance)
        if removed_count < to_remove:
            remaining = sorted(
                [it for it in self._items if it.id not in ids_to_remove],
                key=lambda it: it.timestamp,
            )
            for item in remaining[: to_remove - removed_count]:
                ids_to_remove.append(item.id)

        return self.forget(ids_to_remove)

    # ------------------------------------------------------------------
    #  Inspection
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Total number of stored memory items."""
        return len(self._items)

    def all(self) -> list[MemoryItem]:
        """Return every memory item (use sparingly)."""
        return list(self._items)

    def clear(self) -> None:
        """Remove all memories (both in-memory and on disk)."""
        self._items.clear()
        self._storage.clear()

    def summary(self) -> dict:
        """Return a summary dict of the current memory state."""
        types: dict[str, int] = {}
        total = len(self._items)
        for it in self._items:
            t = it.type.value
            types[t] = types.get(t, 0) + 1
        avg_imp = sum(it.importance for it in self._items) / total if total else 0.0
        return {
            "total": total,
            "by_type": types,
            "avg_importance": round(avg_imp, 3),
            "storage_dir": str(self._storage.path),
        }

    # ------------------------------------------------------------------
    #  Persistence helpers
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Force a full save to disk."""
        self._storage.save_all(self._items)

    def load(self) -> None:
        """Reload all items from disk."""
        self._load()

    def _load(self) -> None:
        self._items = self._storage.load_all()
