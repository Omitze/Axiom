"""Semantic / tag-based search over memory items.

Provides exact-match, substring, and multi-keyword scoring
to retrieve relevant memories without requiring an LLM call.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .models import MemoryItem, MemoryType


class MemorySearch:
    """Stateless search utilities for filtering and ranking MemoryItems."""

    # ------------------------------------------------------------------
    #  Filtering
    # ------------------------------------------------------------------

    @staticmethod
    def by_type(
        items: list[MemoryItem], types: MemoryType | list[MemoryType] | str | None
    ) -> list[MemoryItem]:
        """Filter items to those matching *types* (or return all if ``None``).

        *types* can be a single ``MemoryType``, a string like ``"semantic"``,
        or a list of them. Returns all items when ``None``.
        """
        if types is None:
            return items
        if isinstance(types, str):
            types = [MemoryType(types)]
        elif isinstance(types, MemoryType):
            types = [types]
        type_set = {t.value if isinstance(t, MemoryType) else t for t in types}
        return [it for it in items if it.type.value in type_set]

    @staticmethod
    def by_tag(items: list[MemoryItem], tag: str) -> list[MemoryItem]:
        """Return items that have the given *tag*."""
        return [it for it in items if tag in it.tags]

    @staticmethod
    def by_tags(items: list[MemoryItem], tags: list[str]) -> list[MemoryItem]:
        """Return items that have ALL of the given *tags*."""
        tag_set = set(tags)
        return [it for it in items if tag_set.issubset(set(it.tags))]

    @staticmethod
    def by_importance(
        items: list[MemoryItem], min_imp: float = 0.0, max_imp: float = 1.0
    ) -> list[MemoryItem]:
        """Filter items whose importance falls in [*min_imp*, *max_imp*]."""
        return [it for it in items if min_imp <= it.importance <= max_imp]

    # ------------------------------------------------------------------
    #  Scoring / ranking
    # ------------------------------------------------------------------

    @staticmethod
    def search(
        items: list[MemoryItem],
        query: str,
        n: int = 5,
        types: MemoryType | list[MemoryType] | None = None,
    ) -> list[MemoryItem]:
        """Rank items by relevance to *query* and return the top *n*.

        The scoring function uses a simple TF-like heuristic:
          - exact phrase match in content  → +3 per occurrence
          - word match in content          → +1 per occurrence
          - word match in tags             → +2 per occurrence
          - exact phrase match in tags     → +5 per occurrence

        Items are then sorted by descending score and the top *n* returned.
        Items with zero score are excluded.
        """
        candidates = MemorySearch.by_type(items, types)
        if not candidates:
            return []

        query_lower = query.lower().strip()
        if not query_lower:
            return MemorySearch._ranked_recent(candidates, n)

        words = _tokenize(query_lower)
        scored: list[tuple[float, MemoryItem]] = []

        for item in candidates:
            score = _score_item(item, query_lower, words)
            if score > 0:
                scored.append((score, item))

        # Sort by descending score, then by descending importance as tiebreaker
        scored.sort(key=lambda pair: (-pair[0], -pair[1].importance))

        return [item for _, item in scored[:n]]

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ranked_recent(items: list[MemoryItem], n: int) -> list[MemoryItem]:
        """Return the most recent *n* items when there's no query."""
        sorted_items = sorted(items, key=lambda it: it.timestamp, reverse=True)
        return sorted_items[:n]


# ===========================================================================
#  Tokenisation & scoring helpers
# ===========================================================================


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase word tokens."""
    return re.findall(r"[a-z0-9_\-\/\.]+", text.lower())


def _score_item(item: MemoryItem, query_lower: str, query_words: list[str]) -> float:
    """Compute a relevance score for a single item against the query."""
    content_lower = item.content.lower()
    score = 0.0

    # exact phrase match in content
    score += content_lower.count(query_lower) * 3.0

    # exact phrase match in tags
    tag_text = " ".join(t.lower() for t in item.tags)
    score += tag_text.count(query_lower) * 5.0

    # word matches in content
    content_tokens = Counter(_tokenize(content_lower))
    for w in query_words:
        score += content_tokens.get(w, 0) * 1.0

    # word matches in tags
    tag_tokens = Counter(_tokenize(tag_text))
    for w in query_words:
        score += tag_tokens.get(w, 0) * 2.0

    # Boost by importance (a relevant important memory ranks higher)
    score *= 0.5 + 0.5 * item.importance

    return score
