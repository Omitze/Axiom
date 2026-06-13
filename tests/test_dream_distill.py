"""Tests for the Dream & Distill module.

Tests are organised into six groups:

1. **Schemas** — DreamReport, WorkflowPattern, DistillResult serialisation
2. **PrefixSpan** — frequent sub-sequence mining algorithm
3. **SmartForgetter** — retention scoring and archiving
4. **MemoryConsolidator** — full dream cycle (without LLM)
5. **PatternMiner** — distillation and confidence bands (without LLM)
6. **AutoTrigger** — stateful trigger logic
7. **SkillPackager** — code generation
8. **DreamDistillEngine** — unified entry point
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from pathlib import Path

import pytest

from axiom.dream_distill import (
    AutoTrigger,
    DistillResult,
    DreamDistillEngine,
    DreamReport,
    MemoryConsolidator,
    PatternMiner,
    SkillPackager,
    SmartForgetter,
    WorkflowPattern,
    prefixspan_mine,
)
from axiom.memory import MemoryItem, MemoryManager, MemoryType

# ======================================================================
#  1.  Schemas
# ======================================================================


class TestDreamReport:
    def test_default_zeros(self):
        r = DreamReport()
        assert r.new_items == 0
        assert r.merged_items == 0
        assert r.archived_items == 0
        assert r.strengthened == 0
        assert r.summary == ""
        assert not r  # __bool__ returns False when all zero

    def test_truthy_when_work_done(self):
        assert DreamReport(merged_items=3)
        assert DreamReport(strengthened=1)
        assert DreamReport(archived_items=1)
        assert DreamReport(new_items=1)
        assert not DreamReport()

    def test_to_dict(self):
        r = DreamReport(
            new_items=2,
            merged_items=1,
            strengthened=5,
            summary="Merged conflicts.",
        )
        d = r.to_dict()
        assert d["new_items"] == 2
        assert d["merged_items"] == 1
        assert d["strengthened"] == 5
        assert d["archived_items"] == 0
        assert "Merged" in d["summary"]


class TestWorkflowPattern:
    def test_defaults(self):
        p = WorkflowPattern()
        assert p.name == ""
        assert p.steps == []
        assert p.frequency == 0
        assert p.confidence == 0.0
        assert p.candidate_type == "skill"

    def test_to_dict(self):
        p = WorkflowPattern(
            name="git-sync",
            steps=[{"tool": "bash", "args_summary": "git add ."}],
            frequency=10,
            confidence=0.9,
            contexts=["after editing code"],
        )
        d = p.to_dict()
        assert d["name"] == "git-sync"
        assert d["frequency"] == 10
        assert d["confidence"] == 0.9


class TestDistillResult:
    def test_to_dict(self):
        r = DistillResult(
            patterns=[WorkflowPattern(name="p1"), WorkflowPattern(name="p2")],
            high_confidence=[WorkflowPattern(name="p1", confidence=0.9)],
        )
        d = r.to_dict()
        assert len(d["patterns"]) == 2
        assert len(d["high_confidence"]) == 1
        assert d["high_confidence"][0]["name"] == "p1"


# ======================================================================
#  2.  PrefixSpan
# ======================================================================


class TestPrefixSpanMine:
    def test_empty_returns_empty(self):
        assert prefixspan_mine([], min_support=3) == []
        assert prefixspan_mine([["a", "b"]], min_support=3) == []

    def test_basic_pattern(self):
        """From the zed.md example."""
        seqs = [
            ["read_file", "edit_file", "bash"],
            ["read_file", "edit_file", "write_file"],
            ["read_file", "edit_file", "bash"],
            ["grep", "read_file"],
        ]
        result = prefixspan_mine(seqs, min_support=3)
        # ["read_file", "edit_file"] appears in the first 3 sequences (min_support=3)
        assert ["read_file", "edit_file"] in result
        # The top result should be ["read_file"] (appears in all 4) by frequency
        assert result[0] == ["read_file"]

    def test_min_support_2(self):
        seqs = [
            ["a", "b", "c"],
            ["a", "b"],
            ["a", "c"],
        ]
        result = prefixspan_mine(seqs, min_support=2)
        assert ["a"] in result
        assert ["a", "b"] in result
        assert ["a", "c"] in result

    def test_max_pattern_len(self):
        seqs = [
            ["a", "b", "c", "d"],
            ["a", "b", "c", "d", "e"],
            ["a", "b", "c"],
            ["x", "y"],
        ]
        result = prefixspan_mine(seqs, min_support=2, max_pattern_len=2)
        assert all(len(p) <= 2 for p in result)
        assert ["a"] in result
        assert ["a", "b"] in result

    def test_order_matters(self):
        """Pattern "a, b" should NOT match sequences that have "b, a"."""
        seqs = [
            ["a", "b", "c"],
            ["a", "b", "c"],
            ["b", "a", "c"],
        ]
        result = prefixspan_mine(seqs, min_support=2)
        assert ["a", "b"] in result  # appears in seqs[0], seqs[1]
        assert ["b", "a"] not in result  # only appears in seqs[2] < 2

    def test_deduplicates(self):
        """Same pattern should only appear once in results."""
        seqs = [
            ["a", "b"],
            ["a", "b"],
            ["a", "b"],
        ]
        result = prefixspan_mine(seqs, min_support=3)
        assert result.count(["a", "b"]) == 1  # no duplicates

    def test_single_item_pattern(self):
        seqs = [
            ["read_file"],
            ["read_file"],
            ["read_file"],
            ["grep"],
        ]
        result = prefixspan_mine(seqs, min_support=3)
        assert ["read_file"] in result
        assert ["grep"] not in result

    def test_no_common_pattern(self):
        seqs = [
            ["a"],
            ["b"],
            ["c"],
        ]
        result = prefixspan_mine(seqs, min_support=2)
        assert result == []


# ======================================================================
#  3.  SmartForgetter
# ======================================================================


class TestSmartForgetter:
    def test_evaluate_high_score(self):
        """A fresh, frequently accessed item with high confidence should score well."""
        item = MemoryItem(
            content="important decision",
            importance=0.9,
            metadata={"access_count": 10, "confidence": 0.9},
        )
        score = SmartForgetter().evaluate(item, now=item.timestamp + 1)
        # recency=1/(1+1/86400) ≈ 1, freq≈log(11)≈2.40, conf=0.9 => ≈2.16
        assert score > 1.0

    def test_evaluate_low_score(self):
        """An old, seldom-accessed item should score near zero."""
        item = MemoryItem(
            content="old trivia",
            importance=0.1,
            metadata={"access_count": 0, "confidence": 0.1},
        )
        # Simulate 365 days of age
        score = SmartForgetter().evaluate(item, now=item.timestamp + 86400 * 365)
        # recency=1/(1+365)≈0.0027, freq=log(1)=0, conf=0.1 => ≈0.0
        assert score < 0.01

    def test_evaluate_zero_frequency(self):
        """Frequency of 0 makes log1p(0)=0, so score=0 regardless of other factors."""
        item = MemoryItem(content="never accessed", metadata={"confidence": 0.5})
        score = SmartForgetter().evaluate(item, now=item.timestamp)
        # recency=1/(1+0)=1, freq=log(1)=0, conf=0.5 => 0
        assert score == 0.0

    def test_evaluate_recent_trumps_old(self):
        """Two items with same frequency/confidence: recent one scores higher."""
        now = time.time()
        recent = MemoryItem(
            content="recent",
            timestamp=now - 3600,  # 1 hour ago
            metadata={"access_count": 5, "confidence": 0.5},
        )
        old = MemoryItem(
            content="old",
            timestamp=now - 86400 * 30,  # 30 days ago
            metadata={"access_count": 5, "confidence": 0.5},
        )
        forgetter = SmartForgetter()
        assert forgetter.evaluate(recent, now=now) > forgetter.evaluate(old, now=now)

    def test_archive_below_threshold(self, tmp_path):
        """Items below the threshold should be removed from the manager."""
        mem = MemoryManager(storage_dir=tmp_path / "archive_test")
        # Insert an old, low-value item
        old_item = MemoryItem(
            content="old and low value",
            timestamp=time.time() - 86400 * 100,
            metadata={"access_count": 0, "confidence": 0.05},
        )
        # Manually add to the manager (bypass remember to control timestamp)
        mem._items.append(old_item)
        mem._storage.save_item(old_item)

        fresh_item = mem.remember(
            "fresh and valuable",
            importance=0.9,
            metadata={"access_count": 10, "confidence": 0.9},
        )

        forgetter = SmartForgetter(threshold=0.5)
        archived = forgetter.archive(mem)

        assert old_item.id in archived
        assert fresh_item.id not in archived
        assert mem.count() == 1
        assert mem.get(fresh_item.id) is not None

    def test_archive_no_candidates(self, tmp_path):
        """When all items are above threshold, nothing is archived."""
        mem = MemoryManager(storage_dir=tmp_path / "no_archive")
        mem.remember(
            "high value",
            importance=0.9,
            metadata={"access_count": 10, "confidence": 0.9},
        )
        forgetter = SmartForgetter(threshold=0.001)
        # With such a low threshold, even a low-scoring item survives
        archived = forgetter.archive(mem)
        assert archived == []


# ======================================================================
#  4.  MemoryConsolidator (no LLM)
# ======================================================================


class TestMemoryConsolidator:
    def test_consolidate_no_llm(self, tmp_path):
        """Without LLM, the consolidator should still strengthen and archive."""
        mem = MemoryManager(storage_dir=tmp_path / "dream_test")
        mem.remember("frequent item", metadata={"access_count": 5, "confidence": 0.5})
        mem.remember("single item", metadata={"access_count": 1, "confidence": 0.5})
        # An old stale item to trigger archive
        stale = MemoryItem(
            content="stale",
            timestamp=time.time() - 86400 * 365,
            metadata={"access_count": 0, "confidence": 0.01},
        )
        mem._items.append(stale)
        mem._storage.save_item(stale)

        consolidator = MemoryConsolidator(llm=None)
        report = consolidator.consolidate(mem)

        # Should have strengthened the frequent item
        assert report.strengthened >= 1
        # Should have archived the stale item
        assert report.archived_items >= 1
        # No LLM, so no conflict merging
        assert report.merged_items == 0
        assert report.summary

    def test_consolidate_strengthen_threshold(self, tmp_path):
        """Items with access_count < 3 should not be strengthened."""
        mem = MemoryManager(storage_dir=tmp_path / "strengthen_test")
        low = mem.remember(
            "low access", metadata={"access_count": 2, "confidence": 0.5}
        )
        high = mem.remember(
            "high access", metadata={"access_count": 5, "confidence": 0.5}
        )
        original_conf = high.metadata["confidence"]

        consolidator = MemoryConsolidator(llm=None)
        report = consolidator.consolidate(mem)

        assert report.strengthened >= 1
        # The high-access item should have boosted confidence
        refreshed = mem.get(high.id)
        assert refreshed is not None
        assert refreshed.metadata["confidence"] > original_conf

    def test_merge_conflicts(self, tmp_path):
        """Test the conflict resolution logic directly."""
        mem = MemoryManager(storage_dir=tmp_path / "merge_test")
        ts = time.time()
        item_a = MemoryItem(
            id="a1",
            content="use tabs",
            timestamp=ts - 3600 * 2,
            metadata={"confidence": 0.6},
        )
        item_b = MemoryItem(
            id="b2",
            content="use spaces",
            timestamp=ts - 3600,
            metadata={"confidence": 0.7},
        )
        mem._items.extend([item_a, item_b])
        mem._storage.save_item(item_a)
        mem._storage.save_item(item_b)

        consolidator = MemoryConsolidator(llm=None)
        # Directly call the merge method with a conflict group
        removed = consolidator._merge_conflicts(mem, [item_a, item_b], [["a1", "b2"]])

        # Should remove the older, lower-confidence item (a1)
        assert len(removed) == 1
        assert item_a.id in removed
        assert mem.get(item_b.id) is not None
        # The surviving item should have boosted confidence
        assert mem.get(item_b.id).metadata["confidence"] > 0.7


# ======================================================================
#  5.  PatternMiner (no LLM)
# ======================================================================


class TestPatternMiner:
    def test_mine_empty_sessions(self):
        miner = PatternMiner(llm=None)
        result = miner.mine([])
        assert result.patterns == []
        assert result.high_confidence == []
        assert result.medium_confidence == []

    def test_mine_no_patterns(self):
        """Sessions with no repeated tool calls yield no patterns."""
        miner = PatternMiner(llm=None, min_support=2)
        sessions = [
            {"tools": [{"tool": "read_file"}, {"tool": "bash"}]},
            {"tools": [{"tool": "grep"}, {"tool": "write_file"}]},
        ]
        result = miner.mine(sessions)
        assert result.patterns == []

    def test_mine_discovers_pattern(self):
        """Repeated "read_file -> edit_file" should be discovered."""
        miner = PatternMiner(llm=None, min_support=2)
        sessions = [
            {"tools": [{"tool": "read_file"}, {"tool": "edit_file"}, {"tool": "bash"}]},
            {
                "tools": [
                    {"tool": "read_file"},
                    {"tool": "edit_file"},
                    {"tool": "write_file"},
                ]
            },
            {"tools": [{"tool": "grep"}, {"tool": "read_file"}]},
        ]
        result = miner.mine(sessions)
        assert len(result.patterns) >= 1
        # Auto-generated name should contain the step names
        assert "read_file" in result.patterns[0].name

    def test_confidence_bands(self):
        """Frequent patterns should be high-confidence, infrequent mid."""
        miner = PatternMiner(
            llm=None,
            min_support=2,
            confidence_threshold=0.25,
            confidence_mid=0.15,
        )
        # Create 10 sessions, 8 of which share "a -> b -> c"
        sessions = []
        for i in range(8):
            sessions.append({"tools": [{"tool": "a"}, {"tool": "b"}, {"tool": "c"}]})
        for i in range(2):
            sessions.append({"tools": [{"tool": "x"}, {"tool": "y"}]})

        result = miner.mine(sessions)
        assert len(result.patterns) >= 3  # a, a->b, b, etc.
        # a-b-c pattern has freq=8/10, len=3 => confidence = 0.292
        # With 0.3 mid threshold, a-b-c should be medium confidence
        # With 0.25 high threshold, a-b-c should be high confidence
        assert len(result.high_confidence) >= 1  # a-b-c should be high
        all_names = [p.name for p in result.high_confidence]
        assert any("a" in n and "b" in n for n in all_names)

    def test_mine_from_messages_fallback(self):
        """Sessions in the format of saved session files should also work."""
        miner = PatternMiner(llm=None, min_support=2)
        sessions = [
            {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"function": {"name": "read_file"}},
                            {"function": {"name": "edit_file"}},
                        ],
                    },
                ]
            },
            {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {"function": {"name": "read_file"}},
                            {"function": {"name": "edit_file"}},
                        ],
                    },
                ]
            },
        ]
        result = miner.mine(sessions)
        assert len(result.patterns) >= 1
        assert "read_file" in result.patterns[0].name


# ======================================================================
#  6.  AutoTrigger
# ======================================================================


class TestAutoTrigger:
    def test_should_dream_fresh_no(self, tmp_path):
        """Just started: memory count is low, should not dream."""
        mem = MemoryManager(storage_dir=tmp_path / "trigger_test")
        trigger = AutoTrigger(dream_threshold=50)
        assert not trigger.should_dream(mem)

    def test_should_dream_meets_threshold(self, tmp_path):
        """High memory count and no recent dream."""
        mem = MemoryManager(storage_dir=tmp_path / "trigger_high")
        for i in range(60):
            mem.remember(f"item {i}", metadata={"confidence": 0.5})
        trigger = AutoTrigger(dream_threshold=50, dream_interval=0)
        assert trigger.should_dream(mem)

    def test_should_dream_recently_dreamed(self, tmp_path):
        """Just dreamed — should wait even if count is high."""
        mem = MemoryManager(storage_dir=tmp_path / "trigger_recent")
        for i in range(60):
            mem.remember(f"item {i}", metadata={"confidence": 0.5})
        trigger = AutoTrigger(dream_threshold=50, dream_interval=86400)
        trigger.mark_dreamed()
        assert not trigger.should_dream(mem)

    def test_should_distill_no_workflow_memories(self, tmp_path):
        """No workflow-tagged items — should not distill."""
        mem = MemoryManager(storage_dir=tmp_path / "distill_no_wf")
        trigger = AutoTrigger(distill_threshold=20)
        assert not trigger.should_distill(mem)

    def test_should_distill_meets_threshold(self, tmp_path):
        """Enough workflow items and not recently distilled."""
        mem = MemoryManager(storage_dir=tmp_path / "distill_wf")
        for i in range(25):
            mem.remember(f"wf {i}", tags=["workflow"])
        trigger = AutoTrigger(distill_threshold=20, distill_interval=0)
        assert trigger.should_distill(mem)

    def test_should_distill_recently_distilled(self, tmp_path):
        """Recently distilled — should wait."""
        mem = MemoryManager(storage_dir=tmp_path / "distill_recent")
        for i in range(25):
            mem.remember(f"wf {i}", tags=["workflow"])
        trigger = AutoTrigger(distill_threshold=20, distill_interval=3600)
        trigger.mark_distilled()
        assert not trigger.should_distill(mem)

    def test_mark_methods(self):
        """mark_dreamed and mark_distilled should update internal timestamps."""
        trigger = AutoTrigger()
        trigger.mark_dreamed()
        assert trigger._last_dream > 0
        trigger.mark_distilled()
        assert trigger._last_distill > 0

    def test_to_from_dict(self):
        """Serialisation round-trip."""
        trigger = AutoTrigger()
        trigger.mark_dreamed()
        d = trigger.to_dict()
        restored = AutoTrigger.from_dict(d)
        assert restored._last_dream == pytest.approx(trigger._last_dream, rel=0.01)


# ======================================================================
#  7.  SkillPackager
# ======================================================================


class TestSkillPackager:
    def test_package_generates_code(self, tmp_path):
        """A valid WorkflowPattern should produce code."""
        packager = SkillPackager(output_dir=tmp_path / "skills")
        pattern = WorkflowPattern(
            name="git-sync",
            steps=[
                {"tool": "bash", "args_summary": "git add ."},
                {"tool": "bash", "args_summary": "git commit"},
            ],
            frequency=10,
            confidence=0.9,
        )
        code = packager.package(pattern, llm=None)
        assert code is not None
        assert "class GitSyncTool(Tool)" in code
        assert "create_tool()" in code
        assert "git-sync" in code or pattern.name in code

    def test_package_creates_files(self, tmp_path):
        """Packaging should write files to disk."""
        out = tmp_path / "my_skills"
        packager = SkillPackager(output_dir=out)
        pattern = WorkflowPattern(
            name="test-pattern",
            steps=[{"tool": "bash"}],
            frequency=5,
            confidence=0.85,
        )
        packager.package(pattern, llm=None)
        skill_dir = out / "test-pattern"
        assert skill_dir.is_dir()
        assert (skill_dir / "__init__.py").exists()
        assert (skill_dir / "test-pattern.py").exists()
        content = (skill_dir / "test-pattern.py").read_text()
        assert "TestPatternTool" in content

    def test_package_name_sanitization(self, tmp_path):
        """Special characters in names should be sanitised."""
        packager = SkillPackager(output_dir=tmp_path / "sanitize")
        pattern = WorkflowPattern(
            name="my/cool/pattern",
            steps=[{"tool": "bash"}],
            frequency=3,
            confidence=0.7,
        )
        code = packager.package(pattern, llm=None)
        assert code is not None
        assert "my_cool_pattern" in code.lower() or "my/cool/pattern" not in code

    def test_to_class_name(self):
        assert SkillPackager._to_class_name("git-sync") == "GitSyncTool"
        assert SkillPackager._to_class_name("read_file") == "ReadFileTool"
        assert SkillPackager._to_class_name("deploy-check") == "DeployCheckTool"

    def test_default_body(self):
        """Default body should loop over steps."""
        pattern = WorkflowPattern(
            name="echo-test",
            steps=[{"tool": "bash", "args_summary": "echo hi"}],
            frequency=5,
            confidence=0.9,
        )
        body = SkillPackager._default_body(pattern)
        assert "echo-test" in body or "bash" in body
        assert "results.append" in body or "Execute" in body


# ======================================================================
#  8.  DreamDistillEngine
# ======================================================================


class TestDreamDistillEngine:
    def test_dream_no_memory_manager(self):
        """Without a memory manager, dream should return an empty report."""
        engine = DreamDistillEngine(llm=None, memory_manager=None)
        report = engine.dream()
        assert isinstance(report, DreamReport)
        assert "No memory manager" in report.summary

    def test_dream_with_memory(self, tmp_path):
        """Dream cycle through the engine."""
        mem = MemoryManager(storage_dir=tmp_path / "engine_dream")
        mem.remember("frequent", metadata={"access_count": 10, "confidence": 0.5})
        stale = MemoryItem(
            content="stale",
            timestamp=time.time() - 86400 * 365,
            metadata={"access_count": 0, "confidence": 0.01},
        )
        mem._items.append(stale)
        mem._storage.save_item(stale)

        engine = DreamDistillEngine(llm=None, memory_manager=mem)
        report = engine.dream()
        assert report.strengthened >= 1
        assert report.archived_items >= 1

    def test_distill_no_sessions(self):
        """Without sessions, distill should return empty result."""
        engine = DreamDistillEngine(llm=None)
        result = engine.distill(sessions=[])
        assert isinstance(result, DistillResult)
        assert result.patterns == []

    def test_distill_with_sessions(self, tmp_path):
        """Full distill cycle through the engine."""
        sessions = [
            {"tools": [{"tool": "read_file"}, {"tool": "edit_file"}, {"tool": "bash"}]},
            {
                "tools": [
                    {"tool": "read_file"},
                    {"tool": "edit_file"},
                    {"tool": "write_file"},
                ]
            },
            {"tools": [{"tool": "read_file"}, {"tool": "edit_file"}, {"tool": "bash"}]},
            {"tools": [{"tool": "grep"}]},
        ]
        engine = DreamDistillEngine(llm=None, output_dir=tmp_path / "distilled")
        result = engine.distill(sessions)
        assert len(result.patterns) >= 1
        # The engine uses default thresholds (high=0.8, mid=0.5), which our
        # test data won't reach with the default quality=0.5.  That's fine;
        # patterns are still discovered correctly.
        assert all(
            "read_file" in p.name or "edit_file" in p.name for p in result.patterns
        )

    def test_approve_medium_confidence(self, tmp_path):
        """Approve a medium-confidence pattern."""
        engine = DreamDistillEngine(llm=None, output_dir=tmp_path / "approve_test")
        result = DistillResult(
            medium_confidence=[
                WorkflowPattern(
                    name="my-pattern",
                    steps=[{"tool": "bash"}],
                    frequency=5,
                    confidence=0.7,
                )
            ],
        )
        ok = engine.approve("my-pattern", result)
        assert ok
        assert len(result.high_confidence) == 1
        assert len(result.medium_confidence) == 0

    def test_approve_nonexistent(self, tmp_path):
        """Approving a non-existent pattern returns False."""
        engine = DreamDistillEngine(llm=None, output_dir=tmp_path / "approve_none")
        result = DistillResult()
        ok = engine.approve("nonexistent", result)
        assert not ok

    def test_triggers_marked_after_operations(self, tmp_path):
        """Dream and distill operations should update the trigger timestamps."""
        mem = MemoryManager(storage_dir=tmp_path / "trigger_mark")

        engine = DreamDistillEngine(llm=None, memory_manager=mem)
        engine.dream()
        assert engine.triggers._last_dream > 0

        sessions = [
            {"tools": [{"tool": "a"}, {"tool": "b"}]},
            {"tools": [{"tool": "a"}, {"tool": "b"}]},
            {"tools": [{"tool": "a"}, {"tool": "b"}]},
        ]
        engine.distill(sessions)
        assert engine.triggers._last_distill > 0


# ======================================================================
#  9.  Integration
# ======================================================================


class TestIntegration:
    def test_full_workflow(self, tmp_path):
        """End-to-end: create memories, run dream, run distill."""
        mem = MemoryManager(storage_dir=tmp_path / "integration")
        engine = DreamDistillEngine(
            llm=None, memory_manager=mem, output_dir=tmp_path / "integration_skills"
        )

        # 1. Populate memories
        mem.remember(
            "use ruff for linting", metadata={"access_count": 8, "confidence": 0.6}
        )
        mem.remember(
            "use black for formatting", metadata={"access_count": 2, "confidence": 0.4}
        )
        stale = MemoryItem(
            content="old rule: use flake8",
            timestamp=time.time() - 86400 * 60,
            metadata={"access_count": 0, "confidence": 0.05},
        )
        mem._items.append(stale)
        mem._storage.save_item(stale)

        # 2. Dream
        report = engine.dream()
        assert report.strengthened >= 1
        assert report.archived_items >= 1

        # 3. Distill
        sessions = [
            {
                "tools": [
                    {"tool": "read_file"},
                    {"tool": "edit_file"},
                    {"tool": "bash", "args_summary": "ruff check ."},
                ]
            },
            {
                "tools": [
                    {"tool": "read_file"},
                    {"tool": "edit_file"},
                    {"tool": "bash", "args_summary": "ruff check ."},
                ]
            },
            {
                "tools": [
                    {"tool": "read_file"},
                    {"tool": "edit_file"},
                    {"tool": "bash", "args_summary": "ruff check --fix"},
                ]
            },
        ]
        result = engine.distill(sessions)
        assert len(result.patterns) >= 1

        # 4. Triggers were updated
        assert engine.triggers._last_dream > 0
        assert engine.triggers._last_distill > 0

    def test_prefixspan_with_realistic_tool_names(self):
        """Mine patterns from realistic coding-assistant tool names."""
        seqs = [
            ["grep", "read_file", "edit_file", "bash"],
            ["grep", "read_file", "edit_file", "bash"],
            ["grep", "read_file", "write_file"],
            ["grep", "read_file", "edit_file", "write_file"],
            ["read_file", "edit_file", "bash"],
        ]
        result = prefixspan_mine(seqs, min_support=3)
        # "grep" appears in 4 sequences
        assert ["grep"] in result
        # "grep -> read_file" appears in 4 sequences
        assert ["grep", "read_file"] in result
        # "read_file -> edit_file" appears in 4 sequences
        assert ["read_file", "edit_file"] in result

    def test_smart_forgetter_multiple_items(self, tmp_path):
        """Archive multiple items in a single call."""
        mem = MemoryManager(storage_dir=tmp_path / "multi_archive")
        now = time.time()

        items = [
            MemoryItem(
                id="keep1",
                content="good",
                timestamp=now - 3600,
                metadata={"access_count": 5, "confidence": 0.8},
            ),
            MemoryItem(
                id="keep2",
                content="ok",
                timestamp=now - 86400,
                metadata={"access_count": 3, "confidence": 0.6},
            ),
            MemoryItem(
                id="archive1",
                content="bad old",
                timestamp=now - 86400 * 200,
                metadata={"access_count": 0, "confidence": 0.01},
            ),
            MemoryItem(
                id="archive2",
                content="never used",
                timestamp=now - 86400 * 100,
                metadata={"access_count": 0, "confidence": 0.05},
            ),
        ]
        for item in items:
            mem._items.append(item)
            mem._storage.save_item(item)

        forgetter = SmartForgetter(threshold=0.2)
        archived = forgetter.archive(mem)

        assert "archive1" in archived
        assert "archive2" in archived
        assert "keep1" not in archived
        assert "keep2" not in archived
        assert mem.count() == 2

    def test_prefixspan_edge_cases(self):
        """Edge cases for the PrefixSpan implementation."""
        # Single sequence
        assert prefixspan_mine([["a", "b"]], min_support=2) == []

        # All identical
        result = prefixspan_mine([["a"], ["a"], ["a"]], min_support=3)
        assert result == [["a"]]

        # min_support larger than sequence count
        assert prefixspan_mine([["a"], ["b"]], min_support=10) == []

        # Single item per sequence
        result = prefixspan_mine([["a"], ["a"], ["b"]], min_support=2)
        assert result == [["a"]]

        # Zero min_support
        assert prefixspan_mine([["a"]], min_support=0) == []


# ======================================================================
#  10.  DreamReport edge cases
# ======================================================================


class TestDreamReportEdgeCases:
    def test_custom_summary(self):
        r = DreamReport(new_items=3, summary="3 new items extracted from LLM")
        assert r.summary == "3 new items extracted from LLM"
        assert r  # bool is True

    def test_empty_report_false(self):
        assert not DreamReport()

    def test_to_dict_roundtrip(self):
        r = DreamReport(
            new_items=1,
            merged_items=2,
            archived_items=3,
            strengthened=4,
            summary="done",
        )
        d = r.to_dict()
        r2 = DreamReport(**d)
        assert r2.new_items == r.new_items
        assert r2.merged_items == r.merged_items
        assert r2.archived_items == r.archived_items
        assert r2.strengthened == r.strengthened
        assert r2.summary == r.summary


class TestWorkflowPatternEdgeCases:
    def test_empty_steps(self):
        p = WorkflowPattern(name="empty", steps=[], frequency=0)
        assert p.steps == []
        body = SkillPackager._default_body(p)
        assert "No steps" in body

    def test_various_candidate_types(self):
        for ctype in ("skill", "subagent", "alias"):
            p = WorkflowPattern(name="test", candidate_type=ctype)
            assert p.candidate_type == ctype


class TestDistillResultEdgeCases:
    def test_empty(self):
        r = DistillResult()
        assert r.patterns == []
        assert r.high_confidence == []
        assert r.medium_confidence == []
        assert r.generated_skills == []

    def test_generated_skills_paths(self):
        r = DistillResult(generated_skills=[Path("/tmp/myskill.py")])
        d = r.to_dict()
        # Path serialises differently per OS (\ vs /)
        assert "myskill.py" in d["generated_skills"][0]


class TestMemoryConsolidatorEdgeCases:
    def test_consolidate_empty_memory(self, tmp_path):
        """No items -> zero-value report."""
        mem = MemoryManager(storage_dir=tmp_path / "empty_mem")
        consolidator = MemoryConsolidator(llm=None)
        report = consolidator.consolidate(mem)
        assert report.new_items == 0
        assert report.merged_items == 0
        assert report.archived_items == 0
        assert report.strengthened == 0

    def test_merge_conflicts_empty_groups(self, tmp_path):
        """Empty conflict groups should be silently skipped."""
        mem = MemoryManager(storage_dir=tmp_path / "empty_groups")
        item = mem.remember("something")
        consolidator = MemoryConsolidator(llm=None)
        removed = consolidator._merge_conflicts(mem, [item], [])
        assert removed == []

    def test_merge_conflicts_nonexistent_ids(self, tmp_path):
        """IDs not in the item list should be skipped."""
        mem = MemoryManager(storage_dir=tmp_path / "nonexist_ids")
        item = mem.remember("real item")
        consolidator = MemoryConsolidator(llm=None)
        removed = consolidator._merge_conflicts(
            mem, [item], [["fake_id", "another_fake"]]
        )
        assert removed == []

    def test_find_conflicts_without_llm(self):
        """Without LLM, conflict detection returns empty."""
        consolidator = MemoryConsolidator(llm=None)
        assert consolidator._find_conflicts([]) == []
        assert consolidator._find_conflicts([MemoryItem(content="test")]) == []


class TestPatternMinerEdgeCases:
    def test_mine_invalid_session_format(self):
        """Malformed session dicts should not crash."""
        miner = PatternMiner(llm=None, min_support=2)
        sessions = [
            {"weird_key": "value"},
            {"tools": None},
            [1, 2, 3],
        ]
        result = miner.mine(sessions)
        assert result.patterns == []

    def test_mine_single_session(self):
        """A single session can't form a pattern (min_support=2)."""
        miner = PatternMiner(llm=None, min_support=2)
        sessions = [{"tools": [{"tool": "a"}, {"tool": "b"}]}]
        result = miner.mine(sessions)
        assert result.patterns == []

    def test_mine_with_string_tools(self):
        """Tools can be plain strings, not dicts."""
        miner = PatternMiner(llm=None, min_support=2)
        sessions = [
            {"tools": ["read_file", "edit_file"]},
            {"tools": ["read_file", "edit_file"]},
        ]
        result = miner.mine(sessions)
        assert len(result.patterns) >= 1


class TestSkillPackagerEdgeCases:
    def test_package_empty_steps(self, tmp_path):
        """A pattern with no steps should produce valid but stub code."""
        packager = SkillPackager(output_dir=tmp_path / "empty_steps")
        pattern = WorkflowPattern(name="empty-steps", steps=[], frequency=1)
        code = packager.package(pattern, llm=None)
        assert code is not None
        assert "No steps" in code or "def execute" in code

    def test_output_dir_creation(self, tmp_path):
        """The output dir should be created if it doesn't exist."""
        out = tmp_path / "nonexistent" / "subdir"
        packager = SkillPackager(output_dir=out)
        assert not out.exists()
        pattern = WorkflowPattern(
            name="create-dir-test",
            steps=[{"tool": "bash"}],
            frequency=1,
            confidence=0.5,
        )
        packager.package(pattern, llm=None)
        assert out.exists()
        assert (out / "create-dir-test" / "create-dir-test.py").exists()
