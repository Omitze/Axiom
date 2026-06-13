"""Data models for the Memory module.

Defines the core data structures: MemoryType enum and MemoryItem dataclass.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class MemoryType(str, Enum):
    """Classification of a memory item.

    Three layers mirror biological memory:
    - EPISODIC    : raw conversation turns (what happened)
    - SEMANTIC    : extracted decisions, patterns, facts (what was learned)
    - PROCEDURAL  : reusable work patterns / mini-skills (how to do things)
    """

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"

    # Convenience aliases for clarity at the call site
    CONVERSATION = "episodic"
    DECISION = "semantic"
    PATTERN = "procedural"


@dataclass
class MemoryItem:
    """A single memory entry with importance-based retention.

    Each item is self-contained: content, type, importance score,
    tags for filtering, and optional metadata for extensibility.
    """

    content: str
    type: MemoryType | str = MemoryType.EPISODIC
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # Auto-generated on creation
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """Normalize type and validate importance."""
        if isinstance(self.type, str):
            self.type = MemoryType(self.type)
        if not (0.0 <= self.importance <= 1.0):
            raise ValueError(
                f"importance must be between 0.0 and 1.0, got {self.importance}"
            )

    # -- convenience helpers ------------------------------------------------

    @property
    def age(self) -> float:
        """Seconds since this memory was created."""
        return time.time() - self.timestamp

    def is_expired(self, max_age: float = 86400 * 30) -> bool:
        """Check if the memory is older than *max_age* seconds (default 30 days)."""
        return self.age > max_age

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "content": self.content,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "importance": self.importance,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> MemoryItem:
        """Deserialize from a dict produced by ``to_dict``."""
        return cls(
            id=data["id"],
            content=data["content"],
            type=MemoryType(data["type"]),
            timestamp=data["timestamp"],
            importance=data["importance"],
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
        )

    def __repr__(self) -> str:
        return (
            f"<MemoryItem #{self.id} [{self.type.value}] "
            f"imp={self.importance:.2f} tags={self.tags!r}>"
        )
