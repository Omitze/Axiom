"""Tests for the Memory module — models, search, persistence, manager."""

import json
import time
from pathlib import Path

import pytest

from axiom.memory import (
    MemoryItem,
    MemoryManager,
    MemorySearch,
    MemoryStorage,
    MemoryType,
)

# ============================================================================
#  MEMORY TYPE
# ============================================================================


class TestMemoryType:
    def test_enum_values(self):
        assert MemoryType.EPISODIC.value == "episodic"
        assert MemoryType.SEMANTIC.value == "semantic"
        assert MemoryType.PROCEDURAL.value == "procedural"

    def test_convenience_aliases(self):
        assert MemoryType.CONVERSATION == MemoryType.EPISODIC
        assert MemoryType.DECISION == MemoryType.SEMANTIC
        assert MemoryType.PATTERN == MemoryType.PROCEDURAL

    def test_from_string(self):
        assert MemoryType("episodic") == MemoryType.EPISODIC
        assert MemoryType("semantic") == MemoryType.SEMANTIC
        assert MemoryType("procedural") == MemoryType.PROCEDURAL

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            MemoryType("unknown")


# ============================================================================
#  MEMORY ITEM
# ============================================================================


class TestMemoryItem:
    def test_create_minimal(self):
        item = MemoryItem(content="Hello")
        assert item.content == "Hello"
        assert item.type == MemoryType.EPISODIC
        assert 0.0 <= item.importance <= 1.0
        assert len(item.id) == 12
        assert item.tags == []
        assert item.metadata == {}

    def test_create_full(self):
        item = MemoryItem(
            content="Decision: use JSON",
            type=MemoryType.SEMANTIC,
            importance=0.9,
            tags=["config", "format"],
            metadata={"file": "settings.json"},
        )
        assert item.type == MemoryType.SEMANTIC
        assert item.importance == 0.9
        assert "config" in item.tags
        assert item.metadata["file"] == "settings.json"

    def test_type_from_string(self):
        item = MemoryItem(content="test", type="semantic")
        assert item.type == MemoryType.SEMANTIC

    def test_type_from_lowercase_string(self):
        item = MemoryItem(content="test", type="procedural")
        assert item.type == MemoryType.PROCEDURAL

    def test_importance_out_of_range_raises(self):
        with pytest.raises(ValueError, match="importance"):
            MemoryItem(content="x", importance=1.5)
        with pytest.raises(ValueError, match="importance"):
            MemoryItem(content="x", importance=-0.1)

    def test_importance_boundary(self):
        MemoryItem(content="x", importance=0.0)
        MemoryItem(content="x", importance=1.0)

    def test_age_increases(self):
        item = MemoryItem(content="x")
        a1 = item.age
        time.sleep(0.01)
        assert item.age > a1

    def test_is_expired(self):
        item = MemoryItem(content="x", timestamp=time.time() - 86400 * 31)
        assert item.is_expired(max_age=86400 * 30)
        assert not item.is_expired(max_age=86400 * 40)

    def test_to_dict(self):
        item = MemoryItem(
            content="test content",
            type=MemoryType.SEMANTIC,
            importance=0.7,
            tags=["a", "b"],
            metadata={"k": "v"},
        )
        d = item.to_dict()
        assert d["content"] == "test content"
        assert d["type"] == "semantic"
        assert d["importance"] == 0.7
        assert d["tags"] == ["a", "b"]
        assert d["metadata"] == {"k": "v"}
        assert d["id"] == item.id
        assert d["timestamp"] == item.timestamp

    def test_from_dict(self):
        data = {
            "id": "abc123",
            "content": "restored content",
            "type": "procedural",
            "timestamp": 1000.0,
            "importance": 0.4,
            "tags": ["x"],
            "metadata": {"y": 1},
        }
        item = MemoryItem.from_dict(data)
        assert item.id == "abc123"
        assert item.content == "restored content"
        assert item.type == MemoryType.PROCEDURAL
        assert item.timestamp == 1000.0
        assert item.importance == 0.4
        assert item.tags == ["x"]
        assert item.metadata == {"y": 1}

    def test_from_dict_handles_missing_optional(self):
        data = {
            "id": "x",
            "content": "c",
            "type": "episodic",
            "timestamp": 0.0,
            "importance": 0.5,
        }
        item = MemoryItem.from_dict(data)
        assert item.tags == []
        assert item.metadata == {}

    def test_repr(self):
        item = MemoryItem(
            content="test", type="semantic", importance=0.8, tags=["tag1"]
        )
        r = repr(item)
        assert "MemoryItem" in r
        assert "semantic" in r
        assert "0.80" in r
        assert "tag1" in r


# ============================================================================
#  MEMORY SEARCH
# ============================================================================


@pytest.fixture
def sample_items():
    return [
        MemoryItem(
            content="User asked about file paths in the project",
            type=MemoryType.EPISODIC,
            importance=0.3,
            tags=["conversation", "files"],
        ),
        MemoryItem(
            content="Decision: use UUID v4 for all identifiers",
            type=MemoryType.SEMANTIC,
            importance=0.9,
            tags=["decision", "uuid", "id"],
        ),
        MemoryItem(
            content="Pattern: read config from YAML files with pydantic",
            type=MemoryType.PROCEDURAL,
            importance=0.7,
            tags=["pattern", "config", "yaml"],
        ),
        MemoryItem(
            content="Error: KeyError when accessing missing config key",
            type=MemoryType.SEMANTIC,
            importance=0.6,
            tags=["error", "config", "keyerror"],
        ),
        MemoryItem(
            content="User mentioned they prefer tabs over spaces",
            type=MemoryType.EPISODIC,
            importance=0.1,
            tags=["conversation", "preference"],
        ),
    ]


class TestMemorySearchByType:
    def test_none_returns_all(self, sample_items):
        result = MemorySearch.by_type(sample_items, None)
        assert len(result) == 5

    def test_single_type(self, sample_items):
        result = MemorySearch.by_type(sample_items, MemoryType.SEMANTIC)
        assert len(result) == 2

    def test_list_of_types(self, sample_items):
        result = MemorySearch.by_type(
            sample_items, [MemoryType.SEMANTIC, MemoryType.PROCEDURAL]
        )
        assert len(result) == 3

    def test_empty_result(self):
        result = MemorySearch.by_type([], MemoryType.EPISODIC)
        assert result == []


class TestMemorySearchByTag:
    def test_existing_tag(self, sample_items):
        result = MemorySearch.by_tag(sample_items, "config")
        # items 2 (YAML pattern) and 3 (KeyError) both have "config" tag
        assert len(result) == 2

    def test_nonexistent_tag(self, sample_items):
        result = MemorySearch.by_tag(sample_items, "nonexistent")
        assert result == []


class TestMemorySearchByTags:
    def test_all_tags_present(self, sample_items):
        result = MemorySearch.by_tags(sample_items, ["config", "error"])
        assert len(result) == 1
        assert "KeyError" in result[0].content

    def test_not_all_match(self, sample_items):
        result = MemorySearch.by_tags(sample_items, ["config", "uuid"])
        assert result == []


class TestMemorySearchByImportance:
    def test_min_only(self, sample_items):
        result = MemorySearch.by_importance(sample_items, min_imp=0.6)
        # items with imp >= 0.6: [1] 0.9, [2] 0.7, [3] 0.6
        assert len(result) == 3

    def test_range(self, sample_items):
        result = MemorySearch.by_importance(sample_items, 0.3, 0.7)
        assert len(result) == 3  # 0.3, 0.7, 0.6

    def test_max_only(self, sample_items):
        result = MemorySearch.by_importance(sample_items, max_imp=0.3)
        assert len(result) == 2  # 0.3, 0.1


class TestMemorySearchSearch:
    def test_empty_query_returns_recent(self, sample_items):
        result = MemorySearch.search(sample_items, "", n=3)
        assert len(result) == 3

    def test_keyword_search_finds_match(self, sample_items):
        result = MemorySearch.search(sample_items, "UUID", n=5)
        assert len(result) >= 1
        assert "UUID" in result[0].content

    def test_phrase_search(self, sample_items):
        result = MemorySearch.search(sample_items, "file paths", n=5)
        assert len(result) >= 1
        assert "file paths" in result[0].content

    def test_tag_match_boosted(self, sample_items):
        result = MemorySearch.search(sample_items, "yaml", n=5)
        assert len(result) >= 1
        assert "YAML" in result[0].content

    def test_type_filter(self, sample_items):
        result = MemorySearch.search(
            sample_items, "config", n=5, types=MemoryType.PROCEDURAL
        )
        assert len(result) == 1
        assert result[0].type == MemoryType.PROCEDURAL

    def test_returns_empty_for_no_match(self, sample_items):
        result = MemorySearch.search(sample_items, "zzz_nonexistent_zzz", n=5)
        assert result == []

    def test_empty_items(self):
        result = MemorySearch.search([], "query")
        assert result == []


# ============================================================================
#  MEMORY STORAGE
# ============================================================================


class TestMemoryStorage:
    @pytest.fixture
    def storage(self, tmp_path):
        return MemoryStorage(dir_path=tmp_path / "memory")

    @pytest.fixture
    def item(self):
        return MemoryItem(
            content="test item",
            type=MemoryType.SEMANTIC,
            importance=0.8,
            tags=["test"],
        )

    def test_creates_dir(self, tmp_path):
        path = tmp_path / "new_memory"
        storage = MemoryStorage(dir_path=path)
        assert path.is_dir()

    def test_save_and_load_item(self, storage, item):
        storage.save_item(item)
        loaded = storage.load_item(item.id)
        assert loaded is not None
        assert loaded.content == item.content
        assert loaded.type == item.type
        assert loaded.importance == item.importance

    def test_load_nonexistent(self, storage):
        assert storage.load_item("nonexistent") is None

    def test_delete_item(self, storage, item):
        storage.save_item(item)
        assert storage.exists(item.id)
        assert storage.delete_item(item.id)
        assert not storage.exists(item.id)

    def test_delete_nonexistent(self, storage):
        assert not storage.delete_item("nonexistent")

    def test_exists(self, storage, item):
        assert not storage.exists(item.id)
        storage.save_item(item)
        assert storage.exists(item.id)

    def test_save_all_and_load_all(self, storage):
        items = [
            MemoryItem(content="a", type=MemoryType.EPISODIC),
            MemoryItem(content="b", type=MemoryType.SEMANTIC),
            MemoryItem(content="c", type=MemoryType.PROCEDURAL),
        ]
        storage.save_all(items)
        loaded = storage.load_all()
        assert len(loaded) == 3
        contents = {it.content for it in loaded}
        assert contents == {"a", "b", "c"}

    def test_save_all_replaces(self, storage):
        storage.save_all([MemoryItem(content="old")])
        storage.save_all([MemoryItem(content="new")])
        loaded = storage.load_all()
        assert len(loaded) == 1
        assert loaded[0].content == "new"

    def test_count(self, storage):
        assert storage.count() == 0
        storage.save_item(MemoryItem(content="x"))
        assert storage.count() == 1

    def test_clear(self, storage):
        storage.save_item(MemoryItem(content="x"))
        storage.clear()
        assert storage.count() == 0

    def test_export_json(self, storage, tmp_path):
        items = [
            MemoryItem(content="a", type=MemoryType.EPISODIC),
            MemoryItem(content="b", type=MemoryType.SEMANTIC),
        ]
        storage.save_all(items)
        export_path = tmp_path / "export.json"
        storage.export_json(export_path)
        data = json.loads(export_path.read_text())
        assert len(data) == 2
        contents = {d["content"] for d in data}
        assert contents == {"a", "b"}

    def test_import_json(self, storage, tmp_path):
        data = [
            {
                "id": "i1",
                "content": "imported",
                "type": "episodic",
                "timestamp": 0.0,
                "importance": 0.5,
            },
            {
                "id": "i2",
                "content": "imported2",
                "type": "semantic",
                "timestamp": 0.0,
                "importance": 0.6,
            },
        ]
        import_path = tmp_path / "import.json"
        import_path.write_text(json.dumps(data))
        count = storage.import_json(import_path)
        assert count == 2
        assert storage.count() == 2
        assert storage.load_item("i1") is not None

    def test_import_json_invalid_format(self, storage, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text('{"not": "array"}')
        with pytest.raises(ValueError, match="JSON array"):
            storage.import_json(p)

    def test_corrupted_file_skipped(self, storage):
        # Write a corrupted JSON file
        (storage.dir_path / "corrupt.json").write_text("not json")
        assert storage.load_all() == []

    def test_path_property(self, storage):
        assert storage.path == storage.dir_path


# ============================================================================
#  MEMORY MANAGER
# ============================================================================


class TestMemoryManager:
    @pytest.fixture
    def mem(self, tmp_path):
        return MemoryManager(storage_dir=tmp_path / "test_mem")

    def test_remember_returns_item(self, mem):
        item = mem.remember("hello", type="episodic", importance=0.3)
        assert isinstance(item, MemoryItem)
        assert item.content == "hello"
        assert item.type == MemoryType.EPISODIC
        assert item.importance == 0.3

    def test_remember_persists_to_disk(self, mem):
        item = mem.remember("persistent")
        loaded = mem._storage.load_item(item.id)
        assert loaded is not None
        assert loaded.content == "persistent"

    def test_remember_with_tags_and_metadata(self, mem):
        item = mem.remember(
            "tagged memory",
            type="semantic",
            importance=0.9,
            tags=["important", "decision"],
            metadata={"source": "test"},
        )
        assert "important" in item.tags
        assert item.metadata["source"] == "test"

    def test_remember_defaults(self, mem):
        item = mem.remember("default")
        assert item.type == MemoryType.EPISODIC
        assert item.importance == 0.5
        assert item.tags == []
        assert item.metadata == {}

    def test_recall_empty_query(self, mem):
        mem.remember("first", importance=0.3)
        mem.remember("second", importance=0.9)
        results = mem.recall("", n=1)
        assert len(results) == 1
        assert results[0].content == "second"  # most recent

    def test_recall_keyword_search(self, mem):
        mem.remember("This is about file paths", importance=0.3)
        mem.remember("This is about database", importance=0.3)
        results = mem.recall("file paths", n=5)
        assert len(results) >= 1
        assert "file paths" in results[0].content

    def test_recall_with_type_filter(self, mem):
        mem.remember("chat message", type="episodic", importance=0.3)
        mem.remember("decision: use json", type="semantic", importance=0.8)
        results = mem.recall("decision", n=5, types=MemoryType.SEMANTIC)
        assert len(results) == 1
        assert "json" in results[0].content

    def test_recall_no_match(self, mem):
        mem.remember("hello world")
        results = mem.recall("nonexistent")
        assert results == []

    def test_get_recent(self, mem):
        for i in range(5):
            mem.remember(f"item {i}", importance=0.1)
            time.sleep(0.001)  # ensure distinct timestamps
        recent = mem.get_recent(n=3)
        assert len(recent) == 3
        assert [it.content for it in recent] == ["item 4", "item 3", "item 2"]

    def test_get_recent_with_type_filter(self, mem):
        mem.remember("ep1", type="episodic")
        mem.remember("sem1", type="semantic", importance=0.5)
        mem.remember("ep2", type="episodic")
        results = mem.get_recent(n=5, types=MemoryType.EPISODIC)
        assert len(results) == 2

    def test_get_by_type(self, mem):
        mem.remember("ep", type="episodic")
        mem.remember("sem", type="semantic", importance=0.5)
        assert len(mem.get_by_type(MemoryType.EPISODIC)) == 1
        assert len(mem.get_by_type("semantic")) == 1
        assert len(mem.get_by_type(MemoryType.PROCEDURAL)) == 0

    def test_get_by_tag(self, mem):
        mem.remember("tagged", tags=["important"])
        mem.remember("untagged")
        result = mem.get_by_tag("important")
        assert len(result) == 1

    def test_get_by_importance(self, mem):
        mem.remember("low", importance=0.1)
        mem.remember("high", importance=0.9)
        mem.remember("mid", importance=0.5)
        assert len(mem.get_by_importance(min_imp=0.7)) == 1
        assert len(mem.get_by_importance(max_imp=0.3)) == 1
        assert len(mem.get_by_importance(0.2, 0.6)) == 1

    def test_get_existing_item(self, mem):
        item = mem.remember("find me")
        found = mem.get(item.id)
        assert found is not None
        assert found.content == "find me"

    def test_get_nonexistent(self, mem):
        assert mem.get("nonexistent") is None

    def test_forget_removes_items(self, mem):
        items = [mem.remember(f"item {i}") for i in range(3)]
        removed = mem.forget([items[0].id, items[2].id])
        assert removed == 2
        assert mem.count() == 1
        assert mem.get(items[0].id) is None

    def test_forget_removes_from_disk(self, mem):
        item = mem.remember("to forget")
        mem.forget([item.id])
        assert not mem._storage.exists(item.id)

    def test_forget_nonexistent_id(self, mem):
        removed = mem.forget(["nonexistent"])
        assert removed == 0

    def test_auto_forget_noop_when_under_limit(self, mem):
        for i in range(10):
            mem.remember(f"item {i}", importance=0.5)
        removed = mem.auto_forget(max_items=100)
        assert removed == 0
        assert mem.count() == 10

    def test_auto_forget_removes_low_importance(self, mem):
        for i in range(10):
            mem.remember(f"low_{i}", importance=0.1)
        for i in range(10):
            mem.remember(f"high_{i}", importance=0.9)
        # total 20, max=15, should remove 5 low-importance items
        removed = mem.auto_forget(max_items=15, min_importance=0.5)
        assert removed == 5
        assert mem.count() == 15

    def test_auto_forget_removes_oldest_when_needed(self, mem):
        for i in range(15):
            mem.remember(f"imp_{i}", importance=0.3)
        # total 15, max=5, should remove 10 items
        removed = mem.auto_forget(max_items=5, min_importance=0.2)
        assert removed == 10
        assert mem.count() == 5

    def test_count(self, mem):
        assert mem.count() == 0
        mem.remember("x")
        assert mem.count() == 1

    def test_all(self, mem):
        mem.remember("a")
        mem.remember("b")
        assert len(mem.all()) == 2

    def test_clear(self, mem):
        mem.remember("x")
        mem.remember("y")
        mem.clear()
        assert mem.count() == 0
        assert mem._storage.count() == 0

    def test_summary(self, mem):
        mem.remember("ep", type="episodic", importance=0.5)
        mem.remember("sem", type="semantic", importance=0.9)
        s = mem.summary()
        assert s["total"] == 2
        assert s["by_type"]["episodic"] == 1
        assert s["by_type"]["semantic"] == 1
        assert s["avg_importance"] == 0.7

    def test_summary_empty(self, mem):
        s = mem.summary()
        assert s["total"] == 0
        assert s["avg_importance"] == 0.0

    def test_save_and_load_roundtrip(self, mem):
        mem.remember("persist me", type="semantic", importance=0.8, tags=["a"])
        mem.save()
        # Create a new manager pointing to the same directory
        mem2 = MemoryManager(storage_dir=mem._storage.dir_path)
        assert mem2.count() == 1
        assert mem2.all()[0].content == "persist me"
        assert mem2.all()[0].type == MemoryType.SEMANTIC

    def test_load_reloads_manually(self, mem):
        item = mem.remember("external")
        # Manually create a new item in storage (simulating concurrent addition)
        new_item = MemoryItem(content="added externally", type=MemoryType.EPISODIC)
        mem._storage.save_item(new_item)
        # Load should pick it up
        mem.load()
        assert mem.count() == 2

    def test_load_existing_from_disk(self, tmp_path):
        # Create storage with pre-existing items
        storage = MemoryStorage(dir_path=tmp_path / "preloaded")
        storage.save_item(MemoryItem(content="pre-existing"))
        mem = MemoryManager(storage_dir=storage.dir_path)
        assert mem.count() == 1
        assert mem.all()[0].content == "pre-existing"


# ============================================================================
#  INTEGRATION: memory survives across manager instances
# ============================================================================


class TestIntegration:
    def test_full_workflow(self, tmp_path):
        """Realistic full workflow: remember → recall → forget → persist."""
        mem = MemoryManager(storage_dir=tmp_path / "full_test")

        # Phase 1: store various memories
        mem.remember("User asked about Python imports", type="episodic", importance=0.3)
        mem.remember(
            "Decision: use absolute imports",
            type="semantic",
            importance=0.9,
            tags=["import", "style"],
        )
        mem.remember(
            "Pattern: organize imports: stdlib, third-party, local",
            type="procedural",
            importance=0.7,
            tags=["import", "pattern"],
        )
        mem.remember(
            "Error: ImportError due to circular dependency",
            type="semantic",
            importance=0.6,
            tags=["error", "import"],
        )

        # Phase 2: search
        results = mem.recall("import decision", n=3)
        assert len(results) >= 1

        results = mem.recall("circular dependency", n=5)
        assert len(results) >= 1

        # Phase 3: type filtering
        episodics = mem.get_by_type(MemoryType.EPISODIC)
        assert len(episodics) == 1

        # Phase 4: tag filtering
        import_items = mem.get_by_tag("import")
        assert len(import_items) == 3

        # Phase 5: forget low importance
        mem.auto_forget(max_items=3, min_importance=0.5)
        assert mem.count() == 3  # removes the 0.3 episodic item

        # Phase 6: persistence
        mem.save()
        mem2 = MemoryManager(storage_dir=tmp_path / "full_test")
        assert mem2.count() == 3
        assert mem2.get_by_tag("import") is not None

        # Phase 7: clear
        mem2.clear()
        assert mem2.count() == 0
