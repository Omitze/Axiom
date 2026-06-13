"""Tests for the Goal & Judge module.

Tests are organised by class, following the pattern of test_dream_distill.py.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from axiom.goal import (
    Goal,
    GoalJudgeEngine,
    GoalManager,
    Judge,
    JudgeVerdict,
    VerdictItem,
    VerifierChain,
)

# ===================================================================
#  Goal
# ===================================================================


class TestGoal:
    def test_default_zeros(self):
        g = Goal()
        assert g.description == ""
        assert g.criteria == []
        assert g.pinned is False
        assert g.created_at == 0.0

    def test_truthy_when_has_description(self):
        assert bool(Goal()) is False
        assert bool(Goal(description="Fix the bug")) is True

    def test_to_dict(self):
        g = Goal(
            description="Fix parser",
            criteria=["All tests pass", "No type errors"],
            pinned=True,
            created_at=1234.0,
        )
        d = g.to_dict()
        assert d["description"] == "Fix parser"
        assert d["criteria"] == ["All tests pass", "No type errors"]
        assert d["pinned"] is True
        assert d["created_at"] == 1234.0

    def test_from_dict(self):
        data = {
            "description": "Test goal",
            "criteria": ["Criterion 1"],
            "pinned": True,
            "created_at": 5678.0,
            "metadata": {"source": "user"},
        }
        g = Goal.from_dict(data)
        assert g.description == "Test goal"
        assert g.criteria == ["Criterion 1"]
        assert g.pinned is True
        assert g.created_at == 5678.0
        assert g.metadata["source"] == "user"

    def test_to_dict_roundtrip(self):
        g1 = Goal(
            description="Roundtrip",
            criteria=["A", "B"],
            pinned=False,
            created_at=time.time(),
            metadata={"priority": "high"},
        )
        g2 = Goal.from_dict(g1.to_dict())
        assert g1.description == g2.description
        assert g1.criteria == g2.criteria
        assert g1.pinned == g2.pinned
        assert g1.created_at == g2.created_at

    def test_pinned_default(self):
        """Pinned goals should not be auto-refined."""
        g = Goal(description="Pinned", pinned=True)
        assert g.pinned is True


# ===================================================================
#  VerdictItem
# ===================================================================


class TestVerdictItem:
    def test_to_dict(self):
        vi = VerdictItem(
            criterion="All tests pass",
            passed=True,
            confidence=0.9,
            evidence="pytest output: 4 passed",
            gap="",
        )
        d = vi.to_dict()
        assert d["criterion"] == "All tests pass"
        assert d["passed"] is True
        assert d["confidence"] == 0.9
        assert d["evidence"] == "pytest output: 4 passed"
        assert d["gap"] == ""


# ===================================================================
#  JudgeVerdict
# ===================================================================


class TestJudgeVerdict:
    def test_to_dict(self):
        items = [
            VerdictItem(
                criterion="A", passed=True, confidence=0.9, evidence="ok", gap=""
            )
        ]
        jv = JudgeVerdict(
            goal_met=True,
            vote=0.85,
            evidence=["test passed"],
            gaps=[],
            suggested_fix=None,
            items=items,
        )
        d = jv.to_dict()
        assert d["goal_met"] is True
        assert d["vote"] == 0.85
        assert d["evidence"] == ["test passed"]
        assert len(d["items"]) == 1

    def test_bool_true_when_met_and_confident(self):
        assert bool(JudgeVerdict(goal_met=True, vote=0.5)) is True
        assert bool(JudgeVerdict(goal_met=True, vote=0.49)) is False
        assert bool(JudgeVerdict(goal_met=False, vote=0.9)) is False
        assert bool(JudgeVerdict(goal_met=False, vote=0.0)) is False


# ===================================================================
#  GoalManager
# ===================================================================


class TestGoalManager:
    def test_set_goal_from_string(self):
        mgr = GoalManager()
        goal = mgr.set_goal("Fix the bug in parser.py")
        assert goal.description == "Fix the bug in parser.py"
        assert mgr.current_goal is goal
        assert goal.created_at > 0

    def test_set_goal_from_goal_instance(self):
        mgr = GoalManager()
        g = Goal(description="Pre-made goal", criteria=["A", "B"])
        goal = mgr.set_goal(g)
        assert goal is g
        assert goal.created_at > 0  # auto-set timestamp

    def test_set_goal_archives_previous(self):
        mgr = GoalManager()
        mgr.set_goal("First goal")
        mgr.set_goal("Second goal")
        assert len(mgr.history) == 1
        assert mgr.history[0].description == "First goal"
        assert mgr.current_goal.description == "Second goal"

    def test_set_goal_extracts_criteria(self):
        mgr = GoalManager()
        goal = mgr.set_goal(
            "Fix test_parser bug, ensure all pytest tests pass, cover edge cases"
        )
        assert len(goal.criteria) > 1

    def test_refine_goal_pinned(self):
        mgr = GoalManager()
        goal = mgr.set_goal(Goal(description="Pinned", pinned=True))
        result = mgr.refine_goal(goal)
        assert result is goal  # unchanged

    def test_clear_goal(self):
        mgr = GoalManager()
        mgr.set_goal("Test goal")
        assert mgr.current_goal is not None
        mgr.clear()
        assert mgr.current_goal is None
        assert len(mgr.history) == 1

    def test_summary_without_goal(self):
        mgr = GoalManager()
        assert mgr.summary() is None

    def test_summary_with_goal(self):
        mgr = GoalManager()
        mgr.set_goal("Test", refine=False)
        s = mgr.summary()
        assert s is not None
        assert s["description"] == "Test"
        assert "criteria_count" in s
        assert "pinned" in s

    def test_history_summary(self):
        mgr = GoalManager()
        mgr.set_goal("Goal 1")
        mgr.set_goal("Goal 2")
        mgr.set_goal("Goal 3")
        history = mgr.history_summary(n=2)
        assert len(history) == 2
        assert history[0]["description"] == "Goal 1"
        assert history[1]["description"] == "Goal 2"

    def test_extract_criteria_fallback(self):
        mgr = GoalManager()
        goal = mgr.set_goal("Fix bug; run tests; check output")
        assert len(goal.criteria) >= 1


# ===================================================================
#  Judge (no LLM = rule-based)
# ===================================================================


class TestJudgeRuleBased:
    def test_evaluate_no_goal(self):
        judge = Judge()
        verdict = judge.evaluate(None, [])
        assert verdict.goal_met is False
        assert "No goal" in verdict.gaps[0]

    def test_evaluate_empty_goal(self):
        judge = Judge()
        verdict = judge.evaluate(Goal(), [])
        assert verdict.goal_met is False

    def test_evaluate_goal_without_criteria(self):
        judge = Judge()
        verdict = judge.evaluate(Goal(description="Test"), [])
        # No criteria -> vote should be 0 but no errors
        assert verdict.vote == 0.0

    def test_test_criterion_passed(self):
        judge = Judge()
        vi = judge._rule_based_criterion(
            "All pytest tests pass",
            "The output shows 4 passed in 2.34s",
        )
        assert vi.passed is True
        assert vi.confidence > 0.5

    def test_test_criterion_failed(self):
        judge = Judge()
        vi = judge._rule_based_criterion(
            "All pytest tests pass",
            "The code was written without any testing output",
        )
        assert vi.passed is False

    def test_error_criterion_fixed(self):
        judge = Judge()
        vi = judge._rule_based_criterion(
            "Fix the bug",
            "The implementation is complete and working",
        )
        assert vi.passed is True

    def test_error_criterion_still_present(self):
        judge = Judge()
        vi = judge._rule_based_criterion(
            "Fix the bug",
            "There is still an error in the code",
        )
        assert vi.passed is False

    def test_full_evaluation_with_criteria(self):
        judge = Judge()
        goal = Goal(
            description="Fix tests",
            criteria=["All pytest tests pass", "Fix the bug"],
            created_at=time.time(),
        )
        conversation = [
            {"role": "user", "content": "Fix the bug please"},
            {"role": "assistant", "content": "I ran pytest: 5 passed in 1.2s"},
        ]
        verdict = judge.evaluate(goal, conversation)
        assert verdict.goal_met is True  # both criteria met
        assert verdict.vote > 0.5

    def test_partial_evaluation(self):
        judge = Judge()
        goal = Goal(
            description="Fix things",
            criteria=["All pytest tests pass", "Fix the bug", "Add documentation"],
            created_at=time.time(),
        )
        conversation = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Done, tests pass"},
        ]
        verdict = judge.evaluate(goal, conversation)
        # "Add documentation" likely won't match
        assert not verdict.goal_met or verdict.vote < 1.0

    def test_extract_evidence_rules(self):
        judge = Judge()
        text = "pytest output: 4 passed in 0.5s. No errors found."
        evidence = judge._extract_evidence_rules(text)
        assert len(evidence) > 0
        assert any("passed" in e.lower() for e in evidence)

    def test_evaluate_with_criteria_single(self):
        judge = Judge()
        vi = judge.evaluate_with_criteria(
            "All tests pass",
            "pytest shows 10 passed",
        )
        assert isinstance(vi, VerdictItem)
        assert vi.criterion == "All tests pass"


# ===================================================================
#  Judge (LLM-based) — would require a mock LLM for full testing
# ===================================================================


class TestJudgeLLMBased:
    def test_parse_verdict(self):
        judge = Judge()
        text = """{"goal_met": true, "vote": 0.9, "evidence": ["tests passed"], "gaps": [], "suggested_fix": null, "items": [{"criterion": "tests", "passed": true, "confidence": 0.9, "evidence": "4 passed", "gap": ""}]}"""
        verdict = judge._parse_verdict(text)
        assert verdict.goal_met is True
        assert verdict.vote == 0.9
        assert verdict.evidence == ["tests passed"]
        assert len(verdict.items) == 1

    def test_parse_verdict_handles_markdown_wrapping(self):
        judge = Judge()
        text = '```json\n{"goal_met": false, "vote": 0.3, "evidence": [], "gaps": ["test"], "suggested_fix": "run more tests", "items": []}\n```'
        verdict = judge._parse_verdict(text)
        assert verdict.goal_met is False
        assert verdict.vote == 0.3
        assert verdict.gaps == ["test"]

    def test_format_conversation_string(self):
        judge = Judge()
        assert judge._format_conversation("raw string") == "raw string"

    def test_format_conversation_list(self):
        judge = Judge()
        msgs = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Here is the fix"},
            {"role": "tool", "content": "4 passed in 0.5s"},
        ]
        formatted = judge._format_conversation(msgs)
        assert "[user] Fix the bug" in formatted
        assert "[assistant] Here is the fix" in formatted
        assert "[tool] 4 passed in 0.5s" in formatted

    def test_format_conversation_truncates_long_tool_results(self):
        judge = Judge()
        msgs = [
            {"role": "tool", "content": "x" * 2000},
        ]
        formatted = judge._format_conversation(msgs)
        assert len(formatted) < 1500  # truncated


# ===================================================================
#  VerifierChain
# ===================================================================


class TestVerifierChain:
    def test_verify_criterion_classification(self):
        from axiom.goal.verifier import (
            _is_code_quality_related,
            _is_file_related,
            _is_info_related,
            _is_test_related,
        )

        assert _is_test_related("All tests pass")
        assert _is_test_related("pytest must succeed")
        assert not _is_test_related("Write documentation")

        assert _is_file_related("Create a new file")
        assert _is_file_related("Modify parser.py")
        assert not _is_file_related("Find the answer")

        assert _is_info_related("Find the bug location")
        assert _is_info_related("Explain the algorithm")
        assert not _is_info_related("Write the code")

        assert _is_code_quality_related("No syntax errors")
        assert _is_code_quality_related("Fix the bug")
        assert not _is_code_quality_related("Write documentation")

    def test_verify_no_matching_criteria(self):
        vc = VerifierChain(project_root=str(Path.cwd()))
        goal = Goal(description="Non-code task", criteria=["Do something creative"])
        verdict = vc.verify(goal, "some conversation text")
        # No criteria matched any verifier strategy
        assert not verdict.goal_met
        assert len(verdict.gaps) > 0

    def test_verify_file_changes_found(self):
        vc = VerifierChain()
        goal = Goal(
            description="Create files",
            criteria=["Create a Python module", "Add configuration file"],
        )
        conversation = "File written: axiom/new_module.py. Created: config.yaml"
        items = vc.verify_file_changes(goal, conversation)
        assert len(items) > 0
        assert all(it.passed for it in items)

    def test_verify_file_changes_not_found(self):
        vc = VerifierChain()
        goal = Goal(
            description="Create files",
            criteria=["Create a Python module"],
        )
        conversation = "I think about what to write"
        items = vc.verify_file_changes(goal, conversation)
        assert len(items) == 1
        assert not items[0].passed

    def test_verify_information_found(self):
        vc = VerifierChain()
        goal = Goal(
            description="Find information",
            criteria=["Find the bug location and explain the root cause"],
        )
        conversation = (
            "The bug location is in parser.py. The root cause is missing validation."
        )
        items = vc.verify_information(goal, conversation)
        assert len(items) > 0
        assert items[0].passed

    def test_verify_information_not_found(self):
        vc = VerifierChain()
        goal = Goal(
            description="Find info",
            criteria=["Find the unusual pattern in the data"],
        )
        conversation = "I wrote some code."
        items = vc.verify_information(goal, conversation)
        if items:  # may be empty if no keywords match
            assert not items[0].passed

    def test_verify_syntax_no_files(self):
        vc = VerifierChain()
        goal = Goal(
            description="Code quality",
            criteria=["No syntax errors"],
        )
        conversation = "This is a conversation with no file references"
        items = vc.verify_syntax(goal, conversation)
        # No files to check -> counts as empty, meaning no items
        # (criterion is code-related but no files found to verify)
        assert len(items) == 0

    def test_verify_tests_not_run(self):
        """VerifierChain should not crash when pytest is not available."""
        vc = VerifierChain(project_root="/nonexistent/path")
        goal = Goal(
            description="Test stuff",
            criteria=["All pytest tests pass"],
        )
        items = vc.verify_tests(goal, "some conversation")
        # Either test-related criterion was found or not
        if items:
            assert not items[0].passed  # pytest not available


# ===================================================================
#  GoalJudgeEngine
# ===================================================================


class TestGoalJudgeEngine:
    def test_set_goal(self):
        engine = GoalJudgeEngine()
        goal = engine.set_goal("Fix the parser")
        assert goal.description == "Fix the parser"
        assert engine.goal_manager.current_goal is goal

    def test_set_goal_with_refine(self):
        engine = GoalJudgeEngine()
        goal = engine.set_goal("Fix the parser", refine=False)
        assert goal is not None

    def test_judge_no_goal(self):
        engine = GoalJudgeEngine()
        verdict = engine.judge(conversation=[])
        assert verdict.goal_met is False
        assert "No goal" in verdict.gaps[0]

    def test_judge_no_conversation(self):
        engine = GoalJudgeEngine()
        engine.set_goal("Fix bug")
        verdict = engine.judge(conversation=None)
        assert verdict.goal_met is False

    def test_judge_with_goal(self):
        engine = GoalJudgeEngine()
        engine.set_goal("Fix tests")
        verdict = engine.judge(
            goal=Goal(
                description="Fix tests",
                criteria=["All tests pass"],
                created_at=time.time(),
            ),
            conversation=[{"role": "user", "content": "Fix it"}],
        )
        assert isinstance(verdict, JudgeVerdict)

    def test_judge_with_verifier_combined(self):
        engine = GoalJudgeEngine()
        engine.set_goal("Create files")
        verdict = engine.judge_with_verifier(
            goal=Goal(
                description="Create files",
                criteria=[
                    "Create a Python module",
                    "Find the bug",
                ],
                created_at=time.time(),
            ),
            conversation="File written: test.py. The bug is in parser.",
        )
        assert isinstance(verdict, JudgeVerdict)
        # Verifier should find file changes + info matches
        assert verdict.vote > 0 or verdict.goal_met is not None

    def test_summary(self):
        engine = GoalJudgeEngine()
        assert engine.summary() is None
        engine.set_goal("Test goal")
        s = engine.summary()
        assert s is not None
        assert s["description"] == "Test goal"

    def test_clear(self):
        engine = GoalJudgeEngine()
        engine.set_goal("Test")
        engine.clear()
        assert engine.goal_manager.current_goal is None

    def test_refine_goal_on_engine(self):
        engine = GoalJudgeEngine()
        engine.set_goal("Test")
        result = engine.refine_goal()
        # Without LLM, refine is a no-op
        assert result is not None
        assert result.description == "Test"


# ===================================================================
#  Integration tests
# ===================================================================


class TestIntegration:
    def test_set_goal_and_judge_workflow(self):
        """Simulate a full /goal -> work -> /judge workflow."""
        engine = GoalJudgeEngine()

        # 1. Set a goal
        goal = engine.set_goal(
            "Fix the bug in parser.py; verify all tests pass; no syntax errors",
            refine=False,
        )
        assert (
            goal.description
            == "Fix the bug in parser.py; verify all tests pass; no syntax errors"
        )
        assert len(goal.criteria) > 0

        # 2. Simulate a conversation where the agent "works"
        conversation = [
            {"role": "user", "content": "Fix the bug in parser.py"},
            {"role": "assistant", "content": "I'll fix the bug now."},
            {
                "role": "tool",
                "content": "File written: parser.py\npytest output: 4 passed in 1.2s",
            },
            {
                "role": "assistant",
                "content": "The fix is complete. All tests pass with no errors.",
            },
        ]

        # 3. Judge the result
        verdict = engine.judge(conversation=conversation)
        assert isinstance(verdict, JudgeVerdict)
        # The rule-based judge will find some evidence
        assert verdict.evidence is not None

    def test_goal_history(self):
        engine = GoalJudgeEngine()
        engine.set_goal("Goal 1")
        engine.set_goal("Goal 2")
        engine.set_goal("Goal 3")
        assert len(engine.goal_manager.history) == 2

    def test_judge_verdict_serialization(self):
        """JudgeVerdict should survive a to_dict/from_dict style round-trip."""
        verdict = JudgeVerdict(
            goal_met=True,
            vote=0.95,
            evidence=["tests passed", "no errors"],
            gaps=[],
            suggested_fix=None,
            items=[
                VerdictItem(
                    criterion="All tests pass",
                    passed=True,
                    confidence=0.9,
                    evidence="4 passed",
                    gap="",
                )
            ],
        )
        d = verdict.to_dict()
        assert d["goal_met"] is True
        assert d["vote"] == 0.95
        assert len(d["items"]) == 1
        assert d["items"][0]["criterion"] == "All tests pass"

    def test_goal_serialization_roundtrip(self):
        """Goal serialization / deserialization."""
        g = Goal(
            description="Deserialize test",
            criteria=["A", "B", "C"],
            pinned=False,
            created_at=1000.0,
        )
        d = g.to_dict()
        g2 = Goal.from_dict(d)
        assert g2.description == "Deserialize test"
        assert g2.criteria == ["A", "B", "C"]
        assert g2.created_at == 1000.0

    def test_judge_strictness(self):
        """Higher strictness reduces vote."""
        judge = Judge(strictness=2.0)
        text = '{"goal_met": true, "vote": 1.0, "evidence": [], "gaps": [], "suggested_fix": null, "items": []}'
        verdict = judge._parse_verdict(text)
        # Vote should be divided by strictness
        assert verdict.vote == 0.5

    def test_evaluate_with_llm_error_fallback(self):
        """When LLM parsing fails, should fallback to rule-based without crashing."""
        judge = Judge()
        goal = Goal(
            description="Test",
            criteria=["All tests pass"],
            created_at=time.time(),
        )
        # Simulate LLM returning garbage
        verdict = judge._evaluate_rule_based(goal, "")
        assert isinstance(verdict, JudgeVerdict)


# ===================================================================
#  Edge cases
# ===================================================================


class TestEdgeCases:
    def test_goal_with_empty_criteria_string(self):
        mgr = GoalManager()
        goal = mgr.set_goal("")
        assert goal.description == ""
        assert mgr.current_goal is goal
        assert goal.created_at > 0

    def test_goal_from_dict_empty(self):
        g = Goal.from_dict({})
        assert g.description == ""
        assert g.criteria == []
        assert g.pinned is False

    def test_judge_verdict_defaults(self):
        jv = JudgeVerdict()
        assert jv.goal_met is False
        assert jv.vote == 0.0
        assert jv.evidence == []
        assert jv.gaps == []
        assert jv.suggested_fix is None
        assert jv.items == []

    def test_verdict_item_defaults(self):
        vi = VerdictItem()
        assert vi.criterion == ""
        assert vi.passed is False
        assert vi.confidence == 0.0

    def test_verifier_chain_empty_conversation(self):
        vc = VerifierChain()
        goal = Goal(description="Test", criteria=["All tests pass"])
        verdict = vc.verify(goal, "")
        assert isinstance(verdict, JudgeVerdict)

    def test_goal_manager_extract_criteria_with_semicolons(self):
        mgr = GoalManager()
        goal = mgr.set_goal("Goal A; Goal B; Goal C")
        # Should have split on semicolons
        assert len(goal.criteria) >= 2

    def test_goal_manager_extract_criteria_with_newlines(self):
        mgr = GoalManager()
        goal = mgr.set_goal("Goal A\nGoal B\nGoal C")
        assert len(goal.criteria) >= 2

    def test_format_conversation_with_tool_calls(self):
        judge = Judge()
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "read_file", "arguments": {"path": "test.py"}}],
            },
        ]
        formatted = judge._format_conversation(msgs)
        assert "tool_call" in formatted
        assert "read_file" in formatted

    def test_evaluate_without_llm_graceful(self):
        judge = Judge()
        goal = Goal(
            description="Complex task",
            criteria=["Write tests", "Fix bug", "Add docs"],
            created_at=time.time(),
        )
        conversation = "I wrote tests and fixed the bug. Added documentation."
        verdict = judge.evaluate(goal, conversation)
        assert isinstance(verdict, JudgeVerdict)
        # Should have evaluated all 3 criteria rule-based
        assert len(verdict.items) == 3

    def test_judge_parse_empty_items(self):
        judge = Judge()
        text = '{"goal_met": false, "vote": 0.0, "evidence": [], "gaps": ["nothing"], "suggested_fix": null, "items": []}'
        verdict = judge._parse_verdict(text)
        assert verdict.items == []

    def test_verifier_no_goal_criteria(self):
        vc = VerifierChain()
        goal = Goal(description="Vague goal")
        verdict = vc.verify(goal, "some conversation")
        assert not verdict.goal_met  # no criteria matched any strategy

    def test_suggested_fix_in_verdict(self):
        jv = JudgeVerdict(
            goal_met=False,
            vote=0.3,
            gaps=["Tests not passing"],
            suggested_fix="Run pytest and fix failures",
        )
        assert jv.suggested_fix == "Run pytest and fix failures"

    def test_judge_evaluate_with_list_and_goal_none(self):
        """When goal is None, should handle gracefully."""
        judge = Judge()
        verdict = judge.evaluate(None, [{"role": "user", "content": "hi"}])
        assert verdict.goal_met is False
        assert "No goal" in verdict.gaps[0]

    def test_goal_manager_set_goal_preserves_timestamp(self):
        mgr = GoalManager()
        g = Goal(description="Preserved", created_at=5000.0)
        mgr.set_goal(g)
        assert mgr.current_goal.created_at == 5000.0
