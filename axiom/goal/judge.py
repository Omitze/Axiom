"""The Judge — an independent evaluator for task completion.

The Judge is the "Critic" in the Actor-Critic architecture.
It uses an independent LLM instance (different temperature, prompt, or
even model) to evaluate whether a :class:`Goal` has been truly met,
distinguishing between the Agent's self-reported claims and actual
objective evidence in the conversation.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .schemas import Goal, JudgeVerdict, VerdictItem

if TYPE_CHECKING:
    from axiom.llm import LLM


# ---------------------------------------------------------------------------
#  Judge system prompt
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict, impartial task judge.  Your role is to review the "
    "conversation record and determine whether the stated goal has been "
    "truly achieved.\n\n"
    "Core principles:\n"
    "1. Evaluate each stop condition independently.\n"
    "2. Look for **objective evidence** — do NOT trust the agent's "
    "self-reported claims without verification.\n"
    "   - ❌ Bad: Agent says 'I fixed the bug'\n"
    "   - ✅ Good: pytest output shows '4 passed in 2.34s'\n"
    "3. Check boundary cases — covering only the happy path is NOT enough.\n"
    "4. If you are uncertain, give a low confidence score and explain "
    "what's missing.\n\n"
    "Output ONLY a valid JSON object with this exact structure:\n"
    "{\n"
    '  "goal_met": false,\n'
    '  "vote": 0.0,\n'
    '  "evidence": ["list of concrete pieces of evidence"],\n'
    '  "gaps": ["list of unsatisfied conditions"],\n'
    '  "suggested_fix": "advice on what to do next or null",\n'
    '  "items": [\n'
    "    {\n"
    '      "criterion": "the criterion being evaluated",\n'
    '      "passed": false,\n'
    '      "confidence": 0.0,\n'
    '      "evidence": "specific evidence for this criterion",\n'
    '      "gap": "what is missing or empty string"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Goal to evaluate:\n{goal}\n\n"
    "Conversation:\n{conversation}"
)

# ---------------------------------------------------------------------------
#  Single-criterion evaluation prompt
# ---------------------------------------------------------------------------

_CRITERION_PROMPT = (
    "Evaluate whether the following criterion is satisfied based on the "
    "provided evidence.\n\n"
    "Criterion:\n{criterion}\n\n"
    "Evidence:\n{evidence}\n\n"
    "Return ONLY a JSON object:\n"
    "{{\n"
    '  "passed": true/false,\n'
    '  "confidence": 0.0-1.0,\n'
    '  "evidence": "explain what supports or contradicts this",\n'
    '  "gap": "if not passed, what is missing (empty string if passed)"\n'
    "}}"
)


# ---------------------------------------------------------------------------
#  Judge
# ---------------------------------------------------------------------------


class Judge:
    """Independent evaluator for task completion.

    The Judge uses a *separate* LLM instance from the Agent.  This is
    critical to the Actor-Critic architecture: the Judge should have a
    different temperature (lower), a different system prompt (sceptical),
    and optionally a different model entirely.

    Parameters
    ----------
    llm:
        The LLM instance used for evaluation.  Recommended to use a
        lower-temperature instance than the Agent's.
    strictness:
        A multiplier on the default confidence threshold.  Higher values
        make the Judge harder to satisfy (default 1.0).
    """

    def __init__(self, llm: LLM | None = None, strictness: float = 1.0):
        self.llm = llm
        self.strictness = max(0.1, min(5.0, strictness))

    # ------------------------------------------------------------------
    #  Full evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        goal: Goal | None,
        conversation: list[dict] | str,
    ) -> JudgeVerdict:
        """Evaluate whether the goal has been met based on the conversation.

        Parameters
        ----------
        goal:
            The goal to evaluate.  If ``None``, returns a neutral verdict.
        conversation:
            Either a list of message dicts (from ``agent.messages``) or a
            pre-formatted string.

        Returns
        -------
        JudgeVerdict
            The evaluation result.
        """
        # No goal -> nothing to judge
        if goal is None or not goal.description:
            return JudgeVerdict(
                goal_met=False,
                vote=0.0,
                evidence=[],
                gaps=["No goal has been set."],
            )

        # No LLM -> use rule-based fallback
        if self.llm is None:
            return self._evaluate_rule_based(goal, conversation)

        # Format conversation for the prompt
        conv_text = self._format_conversation(conversation)
        goal_text = goal.description
        if goal.criteria:
            goal_text += "\n\nCriteria:\n" + "\n".join(f"- {c}" for c in goal.criteria)

        prompt = _JUDGE_SYSTEM_PROMPT.format(
            goal=goal_text,
            conversation=conv_text,
        )

        try:
            resp = self.llm.chat(
                messages=[{"role": "system", "content": prompt}],
            )
            verdict = self._parse_verdict(resp.content)
            return verdict
        except Exception as e:
            # Fallback to rule-based on error
            fallback = self._evaluate_rule_based(goal, conversation)
            fallback.metadata = {"error": str(e)}
            return fallback

    # ------------------------------------------------------------------
    #  Single-criterion evaluation
    # ------------------------------------------------------------------

    def evaluate_with_criteria(
        self,
        criterion: str,
        evidence: str,
    ) -> VerdictItem:
        """Evaluate a single condition against given evidence.

        Parameters
        ----------
        criterion:
            The condition text to evaluate.
        evidence:
            The evidence string (conversation excerpts, tool outputs, etc.).

        Returns
        -------
        VerdictItem
            Per-criterion evaluation result.
        """
        if self.llm is None:
            return self._rule_based_criterion(criterion, evidence)

        prompt = _CRITERION_PROMPT.format(criterion=criterion, evidence=evidence)

        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if 0 <= start < end:
                data = json.loads(text[start:end])
                return VerdictItem(
                    criterion=criterion,
                    passed=data.get("passed", False),
                    confidence=data.get("confidence", 0.0),
                    evidence=data.get("evidence", ""),
                    gap=data.get("gap", ""),
                )
        except Exception:
            pass

        return self._rule_based_criterion(criterion, evidence)

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _parse_verdict(self, text: str) -> JudgeVerdict:
        """Parse LLM output into a JudgeVerdict."""
        # Locate the outermost JSON object
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("No JSON object found in LLM response")

        data = json.loads(text[start:end])

        items_data = data.get("items", [])
        items = [
            VerdictItem(
                criterion=it.get("criterion", ""),
                passed=it.get("passed", False),
                confidence=it.get("confidence", 0.0),
                evidence=it.get("evidence", ""),
                gap=it.get("gap", ""),
            )
            for it in items_data
        ]

        return JudgeVerdict(
            goal_met=data.get("goal_met", False),
            vote=data.get("vote", 0.0) / self.strictness,
            evidence=data.get("evidence", []),
            gaps=data.get("gaps", []),
            suggested_fix=data.get("suggested_fix"),
            items=items,
        )

    def _format_conversation(self, conversation: list[dict] | str) -> str:
        """Format conversation messages into a readable string."""
        if isinstance(conversation, str):
            return conversation

        lines: list[str] = []
        for msg in conversation:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")

            # Truncate long tool results
            if role == "tool" and len(content) > 1000:
                content = content[:1000] + "\n... [truncated]"

            if tool_calls:
                for tc in tool_calls:
                    lines.append(f"[{role}] tool_call: {tc.get('name')}(...)")
            elif content:
                # Only show first 200 chars of user/assistant messages for brevity
                display = content[:200] + "..." if len(content) > 200 else content
                lines.append(f"[{role}] {display}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    #  Rule-based fallback (no LLM)
    # ------------------------------------------------------------------

    def _evaluate_rule_based(
        self,
        goal: Goal,
        conversation: list[dict] | str,
    ) -> JudgeVerdict:
        """Rule-based evaluation when no LLM is available."""
        conv_text = (
            " ".join(m.get("content", "") for m in conversation)
            if isinstance(conversation, list)
            else conversation
        )

        items: list[VerdictItem] = []
        total_confidence = 0.0

        for criterion in goal.criteria:
            item = self._rule_based_criterion(criterion, conv_text)
            items.append(item)
            total_confidence += item.confidence

        passed_count = sum(1 for it in items if it.passed)
        criteria_count = len(goal.criteria)

        evidence = self._extract_evidence_rules(conv_text)
        gaps = [it.gap for it in items if not it.passed and it.gap]

        vote = (passed_count / criteria_count) if criteria_count > 0 else 0.0

        return JudgeVerdict(
            goal_met=passed_count == criteria_count and criteria_count > 0,
            vote=vote,
            evidence=evidence,
            gaps=gaps,
            suggested_fix="; ".join(gaps) if gaps else None,
            items=items,
        )

    def _rule_based_criterion(self, criterion: str, evidence: str) -> VerdictItem:
        """Simple keyword-based criterion evaluation (fallback)."""
        # Normalise: lowercase, strip
        c = criterion.lower().strip()
        e = evidence.lower()

        # Test-related criteria
        if "test" in c or "pytest" in c or "pass" in c:
            if "passed" in e or "passing" in e or "success" in e:
                return VerdictItem(
                    criterion=criterion,
                    passed=True,
                    confidence=0.6,
                    evidence="Found test pass indicators in conversation",
                    gap="",
                )
            return VerdictItem(
                criterion=criterion,
                passed=False,
                confidence=0.4,
                evidence="No test pass indicators found",
                gap="No evidence of tests passing",
            )

        # Error-related criteria
        if "error" in c or "bug" in c or "fix" in c:
            if "error" not in e and "traceback" not in e and "failed" not in e:
                return VerdictItem(
                    criterion=criterion,
                    passed=True,
                    confidence=0.5,
                    evidence="No error indicators found after work",
                    gap="",
                )
            return VerdictItem(
                criterion=criterion,
                passed=False,
                confidence=0.3,
                evidence="Error indicators still present",
                gap="Errors or bugs still evident in conversation",
            )

        # Generic fallback: check for keywords from the criterion
        keywords = c.split()
        matches = sum(1 for kw in keywords if len(kw) > 3 and kw in e)
        if matches >= len([k for k in keywords if len(k) > 3]) * 0.5:
            return VerdictItem(
                criterion=criterion,
                passed=True,
                confidence=0.4,
                evidence=f"Found {matches} keyword matches",
                gap="",
            )

        return VerdictItem(
            criterion=criterion,
            passed=False,
            confidence=0.2,
            evidence="Insufficient keyword evidence",
            gap=f"Could not verify: {criterion}",
        )

    def _extract_evidence_rules(self, text: str) -> list[str]:
        """Extract evidence snippets using simple heuristics."""
        evidence: list[str] = []
        patterns = [
            r"passed in \d+\.?\d*s",
            r"\d+ passed",
            r"\d+ failed",
            r"error",
            r"traceback",
            r"success",
            r"File written:",
            r"Created:",
        ]
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            evidence.extend(matches[:3])  # at most 3 per pattern
        return evidence[:10]  # at most 10 total
