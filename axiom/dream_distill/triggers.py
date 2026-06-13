"""Auto-trigger strategy for Dream & Distill cycles.

Instead of requiring the user to manually type ``/dream`` or ``/distill``,
:class:`AutoTrigger` monitors the system state and fires when conditions
are right.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from axiom.memory import MemoryManager

# Default thresholds
_DREAM_THRESHOLD = 50  # memory count before a dream is useful
_DREAM_INTERVAL = 86400  # minimum seconds between dreams (24 h)
_DISTILL_THRESHOLD = 20  # workflow-type memories before a distill
_DISTILL_INTERVAL = 3600  # minimum seconds between distills (1 h)


class AutoTrigger:
    """Check whether it is time to auto-trigger a dream or distill cycle.

    Parameters
    ----------
    dream_threshold:
        Minimum number of memory items before a dream is triggered.
    dream_interval:
        Minimum seconds since the last dream.
    distill_threshold:
        Minimum number of workflow-type memories before a distill.
    distill_interval:
        Minimum seconds since the last distill.
    """

    def __init__(
        self,
        dream_threshold: int = _DREAM_THRESHOLD,
        dream_interval: float = _DREAM_INTERVAL,
        distill_threshold: int = _DISTILL_THRESHOLD,
        distill_interval: float = _DISTILL_INTERVAL,
    ):
        self._last_dream: float = 0.0
        self._last_distill: float = 0.0
        self.dream_threshold = dream_threshold
        self.dream_interval = dream_interval
        self.distill_threshold = distill_threshold
        self.distill_interval = distill_interval

    def should_dream(self, memory_manager: MemoryManager) -> bool:
        """Check whether a dream cycle should be triggered.

        Conditions:
        1. Memory item count exceeds ``dream_threshold``.
        2. At least ``dream_interval`` seconds have passed since the last dream.

        Returns ``True`` if both conditions are met.
        """
        return (
            memory_manager.count() > self.dream_threshold
            and (time.time() - self._last_dream) > self.dream_interval
        )

    def should_distill(self, memory_manager: MemoryManager) -> bool:
        """Check whether a distill cycle should be triggered.

        Conditions:
        1. Number of workflow-type memories (tagged ``"workflow"``) exceeds
           ``distill_threshold``.
        2. At least ``distill_interval`` seconds have passed since the last distill.

        Returns ``True`` if both conditions are met.
        """
        return (
            memory_manager.get_by_tag("workflow").__len__() > self.distill_threshold
            and (time.time() - self._last_distill) > self.distill_interval
        )

    def mark_dreamed(self) -> None:
        """Record that a dream cycle has just completed."""
        self._last_dream = time.time()

    def mark_distilled(self) -> None:
        """Record that a distill cycle has just completed."""
        self._last_distill = time.time()

    # -- serialisation helpers for persistence ------------------------------

    def to_dict(self) -> dict:
        return {
            "last_dream": self._last_dream,
            "last_distill": self._last_distill,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AutoTrigger:
        at = cls()
        at._last_dream = data.get("last_dream", 0.0)
        at._last_distill = data.get("last_distill", 0.0)
        return at
