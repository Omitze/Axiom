"""Axiom Memory Module — multi-layer memory system.

Provides importance-based memory retention with three layers:
- **Episodic**  : raw conversation turns
- **Semantic**  : extracted decisions, patterns, facts
- **Procedural** : reusable work patterns / mini-skills

Typical usage::

    from axiom.memory import MemoryManager, MemoryItem, MemoryType

    mem = MemoryManager()
    mem.remember("User requested feature X", type="episodic", importance=0.3)
    mem.remember("Decision: use config files for X", type="semantic", importance=0.8)

    results = mem.recall("config decision", n=3)
    for r in results:
        print(r.content)
"""

from .manager import MemoryManager
from .models import MemoryItem, MemoryType
from .persistence import MemoryStorage
from .search import MemorySearch

__all__ = [
    "MemoryManager",
    "MemoryItem",
    "MemoryType",
    "MemoryStorage",
    "MemorySearch",
]
