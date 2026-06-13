"""Axiom Goal & Judge Module — task completion goal-setting and evaluation.

This module implements an Actor-Critic architecture for the Agent:

- **Goal** (``/goal``): set formal task-completion conditions that define
  *what counts as done* rather than relying on the Agent's self-assessment.

- **Judge** (``/judge``): an independent evaluator that reviews the
  conversation against the goal's criteria, looking for *objective evidence*
  rather than the Agent's self-reported claims.

- **VerifierChain**: multi-step verification that runs tests, checks file
  modifications, validates syntax, and more — providing hard evidence for
  the Judge.

Typical usage::

    from axiom.goal import GoalJudgeEngine

    engine = GoalJudgeEngine(llm_agent=llm, llm_judge=judge_llm)
    engine.set_goal("Fix the bug in parser.py and make all tests pass")
    engine.set_goal("Fix it", refine=True)  # auto-decompose with LLM

    verdict = engine.judge(conversation=agent.messages)
    if verdict.goal_met:
        print("Goal achieved!")
    else:
        print(f"Still missing: {verdict.gaps}")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .goal import GoalManager
from .judge import Judge
from .schemas import Goal, JudgeVerdict, VerdictItem
from .verifier import VerifierChain

if TYPE_CHECKING:
    from axiom.llm import LLM

__all__ = [
    "GoalJudgeEngine",
    "GoalManager",
    "Judge",
    "VerifierChain",
    "Goal",
    "JudgeVerdict",
    "VerdictItem",
]


class GoalJudgeEngine:
    """Unified entry point for Goal & Judge operations.

    Wraps :class:`GoalManager`, :class:`Judge`, and :class:`VerifierChain`
    into a single object that can be attached to an :class:`Agent`.

    Parameters
    ----------
    llm_agent:
        The Agent's LLM instance (used for goal refinement).
    llm_judge:
        The Judge's LLM instance — should ideally be a *different* instance
        (different temperature, prompt, or model) for the Actor-Critic
        separation.  If ``None``, the Judge will use ``llm_agent`` as
        a fallback (not recommended for production).
    project_root:
        Used by the VerifierChain to run tests etc.
    judge_strictness:
        How strict the Judge is (1.0 = default, 2.0 = twice as strict).
    """

    def __init__(
        self,
        llm_agent: LLM | None = None,
        llm_judge: LLM | None = None,
        project_root: str | None = None,
        judge_strictness: float = 1.0,
    ):
        self.goal_manager = GoalManager(llm=llm_agent)
        self._judge = Judge(llm=llm_judge or llm_agent, strictness=judge_strictness)
        self.verifier = VerifierChain(project_root=project_root)

    # ------------------------------------------------------------------
    #  Goal operations
    # ------------------------------------------------------------------

    def set_goal(
        self,
        input: str | Goal,
        refine: bool = False,
    ) -> Goal:
        """Set a new goal.  Delegates to :attr:`goal_manager`.

        Parameters
        ----------
        input:
            Either a free-form string description, or a :class:`Goal` instance.
        refine:
            Whether to auto-decompose the description using the LLM.
        """
        return self.goal_manager.set_goal(input, refine=refine)

    def refine_goal(self, goal: Goal | None = None) -> Goal | None:
        """Refine the current (or specified) goal."""
        g = goal or self.goal_manager.current_goal
        if g is None:
            return None
        return self.goal_manager.refine_goal(g)

    # ------------------------------------------------------------------
    #  Judge operations
    # ------------------------------------------------------------------

    def judge(
        self,
        goal: Goal | None = None,
        conversation: list[dict] | None = None,
    ) -> JudgeVerdict:
        """Evaluate whether the goal has been met.

        Parameters
        ----------
        goal:
            Goal to evaluate.  Defaults to the current goal.
        conversation:
            Conversation messages.  If ``None``, returns an empty verdict.

        Returns
        -------
        JudgeVerdict
        """
        g = goal or self.goal_manager.current_goal
        if g is None:
            return JudgeVerdict(
                goal_met=False,
                vote=0.0,
                gaps=["No goal has been set. Use /goal <description> first."],
            )
        if not conversation:
            return JudgeVerdict(
                goal_met=False,
                vote=0.0,
                gaps=["No conversation provided for evaluation."],
            )

        return self._judge.evaluate(g, conversation)

    def judge_with_verifier(
        self,
        goal: Goal | None = None,
        conversation: list[dict] | None = None,
    ) -> JudgeVerdict:
        """Run both the LLM-based Judge and the VerifierChain, then combine.

        The combined verdict is stricter: the goal is only met if *both*
        the Judge and the VerifierChain agree.

        Parameters
        ----------
        goal:
            Goal to evaluate.  Defaults to the current goal.
        conversation:
            Conversation messages.  If ``None``, only VerifierChain runs.

        Returns
        -------
        JudgeVerdict
        """
        g = goal or self.goal_manager.current_goal
        if g is None:
            return JudgeVerdict(
                goal_met=False,
                vote=0.0,
                gaps=["No goal has been set."],
            )

        # Run both evaluations
        judge_v = self._judge.evaluate(g, conversation or [])
        verifier_v = self.verifier.verify(g, conversation or "")

        # Combine: goal_met requires both to pass
        combined_met = judge_v.goal_met and verifier_v.goal_met
        combined_vote = (judge_v.vote + verifier_v.vote) / 2.0
        combined_evidence = list(set(judge_v.evidence + verifier_v.evidence))
        combined_gaps = list(set(judge_v.gaps + verifier_v.gaps))
        combined_items = judge_v.items + verifier_v.items

        return JudgeVerdict(
            goal_met=combined_met,
            vote=combined_vote,
            evidence=combined_evidence,
            gaps=combined_gaps,
            suggested_fix=(judge_v.suggested_fix or verifier_v.suggested_fix),
            items=combined_items,
        )

    # ------------------------------------------------------------------
    #  Convenience
    # ------------------------------------------------------------------

    def summary(self) -> dict | None:
        """Return a summary of the current goal, or ``None``."""
        return self.goal_manager.summary()

    def clear(self) -> None:
        """Clear the current goal."""
        self.goal_manager.clear()
