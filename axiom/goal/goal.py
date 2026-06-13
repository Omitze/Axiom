"""Goal management — set, refine, and track task completion conditions.

The :class:`GoalManager` is the central entry point for establishing
formal goals.  It can parse a free-form user description into structured
criteria (via LLM refinement), or accept a fully formed :class:`Goal`
directly.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from .schemas import Goal

if TYPE_CHECKING:
    from axiom.llm import LLM


# ---------------------------------------------------------------------------
#  Default refinement prompt
# ---------------------------------------------------------------------------

_REFINE_PROMPT = (
    "You are a goal-refinement expert.  The user has described a task goal "
    "but it may be vague or incomplete.\n\n"
    "Your job is to decompose it into clear, checkable stop conditions.\n\n"
    "Rules:\n"
    "1. Keep the original intent — do NOT change what the user asked for.\n"
    "2. Break it into the simplest verifiable sub-conditions possible.\n"
    "3. Each criterion must be objectively checkable (e.g. 'pytest passes'\n"
    "   is good; 'code looks clean' is not).\n"
    "4. Include edge cases and boundary conditions where relevant.\n\n"
    "Return ONLY a JSON object with two fields:\n"
    "  - description: a clearer, more precise restatement of the overall goal\n"
    "  - criteria: a list of strings, each being one checkable condition\n\n"
    "Goal description:\n{input}"
)


_CRITERIA_EXTRACTION_PROMPT = (
    "Extract checkable stop conditions from the following goal description. "
    "Return ONLY a JSON list of strings, each being a specific, verifiable "
    "condition.  If the description is already a list of criteria, return it "
    "as-is.\n\n"
    "Goal description:\n{input}"
)


# ---------------------------------------------------------------------------
#  GoalManager
# ---------------------------------------------------------------------------


class GoalManager:
    """Manage the current goal and its lifecycle.

    Parameters
    ----------
    llm:
        LLM instance used for goal refinement.  May be ``None`` to skip
        LLM-dependent steps (useful in unit tests).
    history:
        Optional list of previous goals (for context / trends).
    """

    def __init__(self, llm: LLM | None = None):
        self.llm = llm
        self.current_goal: Goal | None = None
        self.history: list[Goal] = []

    # ------------------------------------------------------------------
    #  Set a goal
    # ------------------------------------------------------------------

    def set_goal(
        self,
        input: str | Goal,
        refine: bool = False,
    ) -> Goal:
        """Set a new goal.

        Parameters
        ----------
        input:
            Either a free-form string description, or an already-constructed
            :class:`Goal` instance.
        refine:
            If ``True`` (and *input* is a string), the manager will attempt
            to decompose the description into structured criteria using the
            LLM.

        Returns
        -------
        Goal
            The newly created (and optionally refined) goal.
        """
        if isinstance(input, Goal):
            goal = input
            if not goal.created_at:
                goal.created_at = time.time()
        else:
            goal = Goal(
                description=input,
                created_at=time.time(),
            )

        # If refinement requested, use LLM to decompose -> criteria
        if refine and isinstance(input, str) and self.llm is not None:
            goal = self.refine_goal(goal)

        # If no refinement but we have a string, extract minimal criteria
        if isinstance(input, str) and not goal.criteria:
            goal.criteria = self._extract_criteria(goal.description)

        # Archive the previous goal
        if self.current_goal is not None:
            self.history.append(self.current_goal)

        self.current_goal = goal
        return goal

    # ------------------------------------------------------------------
    #  Refine a goal via LLM
    # ------------------------------------------------------------------

    def refine_goal(self, goal: Goal) -> Goal:
        """Use the LLM to decompose a vague goal into verifiable sub-conditions.

        Parameters
        ----------
        goal:
            The goal to refine.  Its ``pinned`` field must be ``False``
            (pinned goals are not modified).

        Returns
        -------
        Goal
            The refined goal (same object if pinned or LLM unavailable).
        """
        if goal.pinned or self.llm is None:
            return goal

        prompt = _REFINE_PROMPT.format(input=goal.description)

        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if 0 <= start < end:
                data = json.loads(text[start:end])
                goal.description = data.get("description", goal.description)
                goal.criteria = data.get("criteria", goal.criteria)
        except Exception:
            pass  # graceful fallback — keep the original goal

        return goal

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _extract_criteria(self, description: str) -> list[str]:
        """Extract criteria from a description string.

        If an LLM is available, it attempts structured extraction.
        Otherwise, it falls back to simple heuristics (splitting on
        commas / bullet points).
        """
        if self.llm is not None:
            try:
                prompt = _CRITERIA_EXTRACTION_PROMPT.format(input=description)
                resp = self.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.content.strip()
                start = text.find("[")
                end = text.rfind("]") + 1
                if 0 <= start < end:
                    return json.loads(text[start:end])
            except Exception:
                pass

        # Fallback heuristic: split on newlines, commas, or semicolons
        import re

        parts = re.split(r"[\n;,]+", description)
        parts = [p.strip().strip(".- ") for p in parts if p.strip()]
        return parts if parts else [description]

    # ------------------------------------------------------------------
    #  Inspection
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear the current goal (archive to history)."""
        if self.current_goal is not None:
            self.history.append(self.current_goal)
        self.current_goal = None

    def summary(self) -> dict | None:
        """Return a summary dict of the current goal, or ``None``."""
        if self.current_goal is None:
            return None
        return {
            "description": self.current_goal.description,
            "criteria": list(self.current_goal.criteria),
            "criteria_count": len(self.current_goal.criteria),
            "pinned": self.current_goal.pinned,
            "created_at": self.current_goal.created_at,
        }

    def history_summary(self, n: int = 5) -> list[dict]:
        """Return summaries of the last *n* historical goals."""
        return [
            {
                "description": g.description,
                "criteria_count": len(g.criteria),
                "created_at": g.created_at,
            }
            for g in self.history[-n:]
        ]
