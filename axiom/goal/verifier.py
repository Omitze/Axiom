"""Verifier chain — multi-step verification strategies.

Unlike a single LLM call, the :class:`VerifierChain` runs multiple
independent checks to gather objective evidence:

1. **Code-based goals** → run tests, check exit code, parse test output.
2. **File-modification goals** → diff comparison + syntax checking.
3. **Information-query goals** → keyword matching + completeness assessment.

Each strategy contributes evidence to the final :class:`JudgeVerdict`.
"""

from __future__ import annotations

import re
import subprocess  # noqa: S404 — controlled test execution
import sys
from pathlib import Path

from .schemas import Goal, JudgeVerdict, VerdictItem


class VerifierChain:
    """Multi-step verification that collects objective evidence.

    Each ``verify_*`` method returns a list of :class:`VerdictItem` objects
    that can be merged into a final :class:`JudgeVerdict`.

    Parameters
    ----------
    project_root:
        The project root directory used to resolve relative paths and run
        commands.  Defaults to the current working directory.
    """

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()

    # ------------------------------------------------------------------
    #  Main entry point
    # ------------------------------------------------------------------

    def verify(
        self,
        goal: Goal,
        conversation: list[dict] | str,
    ) -> JudgeVerdict:
        """Run all applicable verifiers and aggregate the results.

        Parameters
        ----------
        goal:
            The goal to verify.
        conversation:
            Conversation messages or pre-formatted string.

        Returns
        -------
        JudgeVerdict
            Aggregated verdict from all applicable verifiers.
        """
        items: list[VerdictItem] = []
        conv_text = (
            " ".join(m.get("content", "") for m in conversation)
            if isinstance(conversation, list)
            else conversation
        )

        # Run all applicable verification strategies
        items.extend(self.verify_tests(goal, conv_text))
        items.extend(self.verify_file_changes(goal, conv_text))
        items.extend(self.verify_information(goal, conv_text))
        items.extend(self.verify_syntax(goal, conv_text))

        # Aggregate results
        if not items:
            return JudgeVerdict(
                goal_met=False,
                vote=0.0,
                gaps=["No verification strategy matched the goal criteria."],
            )

        passed = sum(1 for it in items if it.passed)
        total = len(items)
        vote = passed / total if total > 0 else 0.0

        evidence = [it.evidence for it in items if it.evidence and it.passed]
        gaps = [it.gap for it in items if not it.passed and it.gap]

        return JudgeVerdict(
            goal_met=passed == total,
            vote=vote,
            evidence=evidence,
            gaps=gaps,
            items=items,
        )

    # ------------------------------------------------------------------
    #  Strategy 1: Test verification
    # ------------------------------------------------------------------

    def verify_tests(self, goal: Goal, conv_text: str) -> list[VerdictItem]:
        """Check criteria related to test passing.

        Tries to run ``pytest`` and inspect the output.  If pytest is not
        available or the criterion is not test-related, returns an empty list.
        """
        items: list[VerdictItem] = []
        test_criteria = [c for c in goal.criteria if _is_test_related(c)]

        for criterion in test_criteria:
            try:
                result = subprocess.run(  # noqa: S603
                    [sys.executable, "-m", "pytest", "--tb=short", "-x", "-q"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(self.project_root),
                )
                output = result.stdout + result.stderr

                # Check exit code and output for pass/fail indicators
                passed = result.returncode == 0
                evidence = output[:500] if output else "No test output"
                gap = "" if passed else "Tests failed or had errors"

                items.append(
                    VerdictItem(
                        criterion=criterion,
                        passed=passed,
                        confidence=0.9 if passed else 0.8,
                        evidence=evidence,
                        gap=gap,
                    )
                )
            except FileNotFoundError:
                items.append(
                    VerdictItem(
                        criterion=criterion,
                        passed=False,
                        confidence=0.3,
                        evidence="pytest is not installed or not found",
                        gap="pytest not available to run tests",
                    )
                )
            except subprocess.TimeoutExpired:
                items.append(
                    VerdictItem(
                        criterion=criterion,
                        passed=False,
                        confidence=0.4,
                        evidence="Test execution timed out after 60s",
                        gap="Tests timed out",
                    )
                )
            except Exception as e:
                items.append(
                    VerdictItem(
                        criterion=criterion,
                        passed=False,
                        confidence=0.3,
                        evidence=f"Error running tests: {e}",
                        gap="Could not run tests due to an unexpected error",
                    )
                )

        return items

    # ------------------------------------------------------------------
    #  Strategy 2: File-change verification
    # ------------------------------------------------------------------

    def verify_file_changes(self, goal: Goal, conv_text: str) -> list[VerdictItem]:
        """Check criteria related to file modifications.

        Inspects the conversation for file-change indicators like
        ``File written:``, ``edit_file``, ``Created:``.
        """
        items: list[VerdictItem] = []
        file_criteria = [c for c in goal.criteria if _is_file_related(c)]

        if not file_criteria:
            return items

        # Extract file-change evidence from conversation
        file_pattern = (
            r"(?:File written:|Created:|wrote|created|modified)\s*(?::)?\s*(\S+)"
        )
        file_matches = re.findall(file_pattern, conv_text, re.IGNORECASE)
        changed_files = [f for f in file_matches if Path(f).suffix]

        for criterion in file_criteria:
            passed = len(changed_files) > 0
            evidence = (
                f"Files changed: {', '.join(changed_files[:5])}"
                if changed_files
                else "No file changes detected"
            )

            items.append(
                VerdictItem(
                    criterion=criterion,
                    passed=passed,
                    confidence=0.7 if passed else 0.5,
                    evidence=evidence,
                    gap="" if passed else "No evidence of file modifications",
                )
            )

        return items

    # ------------------------------------------------------------------
    #  Strategy 3: Information-query verification
    # ------------------------------------------------------------------

    def verify_information(self, goal: Goal, conv_text: str) -> list[VerdictItem]:
        """Check criteria related to information gathering / answering.

        Uses keyword overlap and response-length heuristics.
        """
        items: list[VerdictItem] = []
        info_criteria = [c for c in goal.criteria if _is_info_related(c)]

        for criterion in info_criteria:
            # Extract key terms from the criterion (words longer than 3 chars)
            key_terms = [w.lower() for w in criterion.split() if len(w) > 3]
            if not key_terms:
                continue

            matches = sum(1 for t in key_terms if t in conv_text.lower())
            coverage = matches / len(key_terms) if key_terms else 0.0

            passed = coverage >= 0.6
            items.append(
                VerdictItem(
                    criterion=criterion,
                    passed=passed,
                    confidence=min(0.9, 0.3 + coverage * 0.6),
                    evidence=(
                        f"Found {matches}/{len(key_terms)} key terms in conversation"
                    ),
                    gap="" if passed else f"Only {coverage:.0%} of key terms covered",
                )
            )

        return items

    # ------------------------------------------------------------------
    #  Strategy 4: Syntax verification
    # ------------------------------------------------------------------

    def verify_syntax(self, goal: Goal, conv_text: str) -> list[VerdictItem]:
        """Check Python file syntax for criteria related to code quality.

        Scans the conversation for Python file paths and runs a syntax check
        on any files that appear to have been created or modified.
        """
        items: list[VerdictItem] = []
        code_criteria = [c for c in goal.criteria if _is_code_quality_related(c)]

        if not code_criteria:
            return items

        # Find Python files mentioned in the conversation
        py_files = set(
            m.group(1)
            for m in re.finditer(
                r"(?:File written:|Created:|modified|wrote)\s*(?::)?\s*(\S+\.py)",
                conv_text,
                re.IGNORECASE,
            )
        )

        syntax_errors: list[str] = []
        for fname in py_files:
            fpath = self.project_root / fname
            if fpath.exists():
                try:
                    compile(fpath.read_text(encoding="utf-8"), fname, "exec")
                except SyntaxError as e:
                    syntax_errors.append(f"{fname}: {e}")

        # Only report if we found actual files to check
        if not py_files:
            return items

        for criterion in code_criteria:
            passed = len(syntax_errors) == 0
            evidence = (
                f"Checked {len(py_files)} file(s), no syntax errors"
                if passed
                else f"Syntax errors in: {'; '.join(syntax_errors[:3])}"
            )

            items.append(
                VerdictItem(
                    criterion=criterion,
                    passed=passed,
                    confidence=0.8,
                    evidence=evidence,
                    gap=""
                    if passed
                    else f"Syntax errors: {', '.join(syntax_errors[:3])}",
                )
            )

        return items


# ---------------------------------------------------------------------------
#  Helper predicates — determine which verifier strategy to apply
# ---------------------------------------------------------------------------


_TEST_KEYWORDS = {"test", "pytest", "unittest", "pass", "fail", "coverage"}
_FILE_KEYWORDS = {"file", "write", "create", "modify", "edit", "add", "change"}
_INFO_KEYWORDS = {"find", "search", "list", "show", "tell", "explain", "what", "how"}
_CODE_KEYWORDS = {"syntax", "type error", "lint", "compile", "quality", "style"}


def _is_test_related(criterion: str) -> bool:
    c = criterion.lower()
    return any(kw in c for kw in _TEST_KEYWORDS)


def _is_file_related(criterion: str) -> bool:
    c = criterion.lower()
    return any(kw in c for kw in _FILE_KEYWORDS) or ".py" in c


def _is_info_related(criterion: str) -> bool:
    c = criterion.lower()
    return any(kw in c for kw in _INFO_KEYWORDS)


def _is_code_quality_related(criterion: str) -> bool:
    c = criterion.lower()
    return any(kw in c for kw in _CODE_KEYWORDS) or "error" in c or "bug" in c
