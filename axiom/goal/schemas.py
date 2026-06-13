"""Data models for the Goal & Judge module.

Defines three core data structures:
- **Goal**: a formal task completion condition set by the user (or refined by LLM)
- **JudgeVerdict**: the result of evaluating whether a goal has been met
- **VerdictItem**: a per-criterion evaluation item
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Goal:
    """A formal task-completion condition.

    The Goal is the "north star" for the Agent — instead of optimistically
    deciding when it's done, the Agent defers to the Judge to verify whether
    this Goal's criteria are truly satisfied.

    Attributes
    ----------
    description:
        Human-readable description, e.g. "Fix the bug in test_parser and
        ensure all tests pass".
    criteria:
        List of checkable stop conditions, e.g.
        ``["All pytest tests pass", "Edge case X is covered", "No type errors"]``.
    pinned:
        If ``True``, the Agent is not allowed to modify this goal automatically.
        If ``False``, the Agent may refine / decompose it via :meth:`GoalManager.refine_goal`.
    created_at:
        Unix timestamp of creation.
    metadata:
        Optional extensibility dict (store source, priority, etc.).
    """

    description: str = ""
    criteria: list[str] = field(default_factory=list)
    pinned: bool = False
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "description": self.description,
            "criteria": list(self.criteria),
            "pinned": self.pinned,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Goal:
        """Deserialize from a dict produced by ``to_dict``."""
        return cls(
            description=data.get("description", ""),
            criteria=list(data.get("criteria", [])),
            pinned=data.get("pinned", False),
            created_at=data.get("created_at", 0.0),
            metadata=dict(data.get("metadata", {})),
        )

    def __bool__(self) -> bool:
        """A Goal is truthy when it has a description."""
        return bool(self.description)


@dataclass
class VerdictItem:
    """Evaluation result for a single criterion.

    Attributes
    ----------
    criterion:
        The criterion text being evaluated.
    passed:
        Whether the criterion was satisfied.
    confidence:
        How confident the Judge is in this verdict (0.0 - 1.0).
    evidence:
        Specific evidence from the conversation supporting this verdict.
    gap:
        If not passed, what is missing.
    """

    criterion: str = ""
    passed: bool = False
    confidence: float = 0.0
    evidence: str = ""
    gap: str = ""

    def to_dict(self) -> dict:
        return {
            "criterion": self.criterion,
            "passed": self.passed,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "gap": self.gap,
        }


@dataclass
class JudgeVerdict:
    """The outcome of evaluating a Goal against a conversation.

    Attributes
    ----------
    goal_met:
        Whether the overall goal was achieved.
    vote:
        Composite confidence score (0.0 - 1.0). Low values suggest the
        criteria are ambiguous or there is insufficient evidence.
    evidence:
        Key pieces of evidence extracted from the conversation that
        support the judgment.
    gaps:
        List of unsatisfied conditions and what is missing for each.
    suggested_fix:
        Optional advice on how to proceed if the goal is not met.
    items:
        Per-criterion evaluation breakdown.
    """

    goal_met: bool = False
    vote: float = 0.0
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    suggested_fix: str | None = None
    items: list[VerdictItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "goal_met": self.goal_met,
            "vote": self.vote,
            "evidence": list(self.evidence),
            "gaps": list(self.gaps),
            "suggested_fix": self.suggested_fix,
            "items": [it.to_dict() for it in self.items],
        }

    def __bool__(self) -> bool:
        """A verdict is truthy when the goal is met with reasonable confidence."""
        return self.goal_met and self.vote >= 0.5
