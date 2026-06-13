"""Axiom Dream & Distill Module — memory consolidation and workflow distillation.

This module implements two "meta-cognitive" capabilities inspired by
the human sleep-wake cycle:

- **Dream** (``/dream``): consolidate noisy memories, resolve contradictions,
  strengthen high-value items, archive stale ones.

- **Distill** (``/distill``): mine session histories for repeated tool-call
  workflows, then package them as reusable Skills.

Typical usage::

    from axiom.dream_distill import DreamDistillEngine

    engine = DreamDistillEngine(llm=llm, memory_manager=mem)
    report = engine.dream()
    result = engine.distill(sessions)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .distill import PatternMiner, SkillPackager, prefixspan_mine
from .dream import MemoryConsolidator, SmartForgetter
from .schemas import DistillResult, DreamReport, WorkflowPattern
from .triggers import AutoTrigger

if TYPE_CHECKING:
    from pathlib import Path

    from axiom.llm import LLM
    from axiom.memory import MemoryManager

__all__ = [
    "DreamDistillEngine",
    "MemoryConsolidator",
    "SmartForgetter",
    "PatternMiner",
    "SkillPackager",
    "AutoTrigger",
    "prefixspan_mine",
    "DreamReport",
    "WorkflowPattern",
    "DistillResult",
]


class DreamDistillEngine:
    """Unified entry point for Dream & Distill operations.

    Wraps :class:`MemoryConsolidator`, :class:`PatternMiner`,
    :class:`SkillPackager`, and :class:`AutoTrigger` into a single
    object that can be attached to an :class:`Agent`.

    Parameters
    ----------
    llm:
        LLM instance used for semantic analysis (naming, contradiction
        detection, code generation).  May be ``None`` for testing.
    memory_manager:
        The active memory system to consolidate.
    output_dir:
        Directory for generated skill files.  Defaults to
        ``~/.axiom/skills/``.
    """

    def __init__(
        self,
        llm: LLM | None = None,
        memory_manager: MemoryManager | None = None,
        output_dir: str | Path | None = None,
    ):
        self.llm = llm
        self.memory_manager = memory_manager
        self.consolidator = MemoryConsolidator(llm=llm)
        self.miner = PatternMiner(llm=llm)
        self.packager = SkillPackager(output_dir=output_dir)
        self.triggers = AutoTrigger()

    # ------------------------------------------------------------------
    #  Dream
    # ------------------------------------------------------------------

    def dream(self, memory_manager: MemoryManager | None = None) -> DreamReport:
        """Run a memory-consolidation (dream) cycle.

        Parameters
        ----------
        memory_manager:
            Override the engine's default memory manager for this call.
        """
        mem = memory_manager or self.memory_manager
        if mem is None:
            return DreamReport(summary="No memory manager available.")

        report = self.consolidator.consolidate(mem)
        self.triggers.mark_dreamed()
        return report

    # ------------------------------------------------------------------
    #  Distill
    # ------------------------------------------------------------------

    def distill(
        self,
        sessions: list[dict] | None = None,
        memory_manager: MemoryManager | None = None,
    ) -> DistillResult:
        """Run a workflow-distillation cycle.

        Parameters
        ----------
        sessions:
            List of serialised session dicts.  If ``None``, only patterns
            from memory are used (future extension).
        memory_manager:
            Override the engine's default memory manager for this call.
        """
        mem = memory_manager or self.memory_manager
        if not sessions:
            return DistillResult()

        result = self.miner.mine(sessions, memory_manager=mem)

        # Package high-confidence patterns as skill files
        for pattern in result.high_confidence:
            code = self.packager.package(pattern, llm=self.llm)
            if code:
                skill_path = self.packager.output_dir / pattern.name
                result.generated_skills.append(skill_path / f"{pattern.name}.py")

        self.triggers.mark_distilled()
        return result

    # ------------------------------------------------------------------
    #  Approve a medium-confidence pattern
    # ------------------------------------------------------------------

    def approve(self, pattern_name: str, result: DistillResult) -> bool:
        """Manually approve a medium-confidence pattern for packaging.

        Parameters
        ----------
        pattern_name:
            The name of the pattern to approve.
        result:
            The :class:`DistillResult` from the last distill pass.

        Returns
        -------
        bool
            ``True`` if the pattern was approved and packaged.
        """
        for pattern in result.medium_confidence:
            if pattern.name == pattern_name:
                code = self.packager.package(pattern, llm=self.llm)
                if code:
                    result.high_confidence.append(pattern)
                    result.medium_confidence.remove(pattern)
                    skill_path = self.packager.output_dir / pattern.name
                    result.generated_skills.append(skill_path / f"{pattern.name}.py")
                    return True
        return False
