"""Memory consolidation — the "dream" phase.

Given a set of raw memories, the dream cycle:

1. **Detect contradictions** — LLM identifies conflicting pairs
   (e.g. "use tabs" vs "use spaces").
2. **Merge duplicates** — newer, higher-confidence items survive.
3. **Strengthen high-reuse memories** — boost confidence of frequently
   accessed items.
4. **Archive stale memories** — low-value items moved to cold storage.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from .schemas import DreamReport

if TYPE_CHECKING:
    from axiom.llm import LLM
    from axiom.memory import MemoryItem, MemoryManager


# ---------------------------------------------------------------------------
#  MemoryConsolidator
# ---------------------------------------------------------------------------


class MemoryConsolidator:
    """Orchestrate a memory-consolidation / dream cycle.

    Parameters
    ----------
    llm:
        The LLM instance used for contradiction detection.  May be ``None``
        to skip LLM-dependent steps (useful in unit tests).
    recency_window:
        Only items younger than this many seconds participate in the cycle.
    """

    _CONTRADICTION_PROMPT = (
        "You are a memory-consolidation expert.  I will give you a list of "
        "memory items.  Identify any pairs that are contradictory "
        "(e.g. 'use tabs' vs 'use spaces').  Return ONLY a JSON list of "
        "conflict groups, each group being a list of item IDs.  "
        "If there are no conflicts return [].\n\nMemories:\n{memories}"
    )

    def __init__(self, llm: LLM | None = None, recency_window: float = 86400 * 7):
        self.llm = llm
        self.recency_window = recency_window  # seconds, default 7 days

    def consolidate(self, memory_manager: MemoryManager) -> DreamReport:
        """Run one dream cycle.

        Returns a :class:`DreamReport` summarising what happened.
        """
        report = DreamReport()

        # 1. Gather recent memories
        recent = memory_manager.get_recent(n=200)

        # 2. Detect contradictions with LLM
        conflicts = self._find_conflicts(recent) if self.llm is not None else []

        # 3. Merge contradictory pairs
        merged_ids = self._merge_conflicts(memory_manager, recent, conflicts)
        report.merged_items = len(merged_ids)

        # 4. Strengthen high-frequency memories
        strengthened = self._strengthen(memory_manager, recent)
        report.strengthened = len(strengthened)

        # 5. Archive stale / low-value memories
        archived = self._archive(memory_manager, recent)
        report.archived_items = len(archived)

        report.summary = (
            f"Dream cycle complete: merged {report.merged_items}, "
            f"strengthened {report.strengthened}, "
            f"archived {report.archived_items}."
        )
        return report

    # -- internal helpers ---------------------------------------------------

    def _find_conflicts(self, items: list[MemoryItem]) -> list[list[str]]:
        """Ask the LLM to find contradictory memory pairs.

        Returns a list of groups; each group is a list of item IDs that
        contradict each other.
        """
        if not items or self.llm is None:
            return []

        lines = [f"- {it.id}: {it.content[:200]}" for it in items]
        prompt = self._CONTRADICTION_PROMPT.format(memories="\n".join(lines))

        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content.strip()
            # Locate the outermost JSON array
            start = text.find("[")
            end = text.rfind("]") + 1
            if 0 <= start < end:
                return json.loads(text[start:end])
        except Exception:
            pass  # graceful fallback — no LLM = skip conflict detection

        return []

    def _merge_conflicts(
        self,
        memory_manager: MemoryManager,
        items: list[MemoryItem],
        conflicts: list[list[str]],
    ) -> list[str]:
        """Merge each conflict group into a single, canonical item.

        Resolution rules (in order):
        1. Most recent timestamp wins.
        2. Higher confidence wins (tie-breaker).
        3. Longer / more specific content wins (final tie-breaker).

        Returns the list of item IDs that were removed.
        """
        item_by_id = {it.id: it for it in items}
        removed_ids: list[str] = []

        for group in conflicts:
            group_items = [item_by_id[g] for g in group if g in item_by_id]
            if len(group_items) < 2:
                continue

            # Sort by resolution rules: timestamp desc, confidence desc, length desc
            canonical = max(
                group_items,
                key=lambda it: (
                    it.timestamp,
                    it.metadata.get("confidence", 0.5),
                    len(it.content),
                ),
            )

            to_remove = [it.id for it in group_items if it.id != canonical.id]
            if to_remove:
                memory_manager.forget(to_remove)
                removed_ids.extend(to_remove)
                # Boost the canonical item's confidence
                old_conf = canonical.metadata.get("confidence", 0.5)
                canonical.metadata["confidence"] = min(1.0, old_conf + 0.1)

        return removed_ids

    def _strengthen(
        self, memory_manager: MemoryManager, items: list[MemoryItem]
    ) -> list[str]:
        """Boost confidence of memories that have been accessed frequently.

        Returns list of item IDs that were strengthened.
        """
        strengthened: list[str] = []
        for it in items:
            access_count = it.metadata.get("access_count", 0)
            if access_count >= 3:
                old_conf = it.metadata.get("confidence", 0.5)
                new_conf = min(1.0, old_conf + 0.05)
                if new_conf != old_conf:
                    it.metadata["confidence"] = new_conf
                    strengthened.append(it.id)
        return strengthened

    def _archive(
        self, memory_manager: MemoryManager, items: list[MemoryItem]
    ) -> list[str]:
        """Move stale, low-value memories to cold storage.

        Delegates to :class:`SmartForgetter` for the actual scoring.
        """
        forgetter = SmartForgetter(threshold=0.15)
        return forgetter.archive(memory_manager, items)


# ---------------------------------------------------------------------------
#  SmartForgetter
# ---------------------------------------------------------------------------


class SmartForgetter:
    """Intelligent memory decay based on recency, frequency, and confidence.

    Rather than deleting old memories outright, we *archive* them (move them
    to a cold-storage layer where they don't participate in everyday retrieval).
    """

    def __init__(self, threshold: float = 0.1):
        self.threshold = threshold

    def evaluate(self, item: MemoryItem, now: float | None = None) -> float:
        """Compute a retention score for a memory item.

        The formula combines three signals:

        - **Recency**: ``1 / (1 + days_since_access)``
        - **Frequency**: ``log(1 + access_count)``
        - **Confidence**: ``item.metadata.get("confidence", 0.5)``

        Returns a score in ``[0, inf)``.  Lower values are better candidates
        for archiving.
        """
        if now is None:
            import time

            now = time.time()

        days_since_access = max(0.0, (now - item.timestamp) / 86400.0)
        access_count = item.metadata.get("access_count", 0)

        recency = 1.0 / (1.0 + days_since_access)
        frequency = math.log1p(access_count)  # log(1 + x)
        confidence = item.metadata.get("confidence", 0.5)

        return recency * frequency * confidence

    def archive(
        self,
        memory_manager: MemoryManager,
        items: list[MemoryItem] | None = None,
        threshold: float | None = None,
    ) -> list[str]:
        """Archive items with scores below the threshold.

        *Archiving* means the item is removed from active memory but
        conceptually moved to "cold storage" (for now, simply deleted).

        Parameters
        ----------
        memory_manager:
            The active memory store.
        items:
            The candidate pool.  If ``None``, uses all items from the manager.
        threshold:
            Score cut-off.  Defaults to the instance's ``threshold``.

        Returns the list of archived item IDs.
        """
        threshold = threshold if threshold is not None else self.threshold
        candidates = items if items is not None else memory_manager.all()
        if not candidates:
            return []

        ids_to_archive = [it.id for it in candidates if self.evaluate(it) < threshold]
        if ids_to_archive:
            memory_manager.forget(ids_to_archive)
        return ids_to_archive
