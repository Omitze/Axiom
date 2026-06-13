"""Tests for the Skills module — registry, loader, manager, generator, builtins."""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from axiom.skills import (
    SkillError,
    SkillLoader,
    SkillManager,
    SkillNotFoundError,
    SkillRegistry,
    SkillValidationError,
    ValidationResult,
    generate_skill,
    load_skill,
    validate_skill,
)
from axiom.skills.loader import _find_skills_in_dir, _load_skill_from_file
from axiom.tools import ALL_TOOLS, get_tool
from axiom.tools.base import Tool

# ============================================================================
#  FAKE SKILL — used by several tests
# ============================================================================

VALID_SKILL_CODE = '''
"""A test skill for unit tests."""

from axiom.tools.base import Tool


class HelloTool(Tool):
    name = "hello"
    description = "Say hello to someone."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Who to greet"},
        },
        "required": ["name"],
    }

    def execute(self, name: str) -> str:
        return f"Hello, {name}!"


def create_tool() -> Tool:
    return HelloTool()
'''


# ============================================================================
#  REGISTRY
# ============================================================================


class TestSkillRegistry:
    def test_register_and_get(self):
        reg = SkillRegistry()
        tool = _make_fake_tool("greeter", "says hello")
        reg.register(tool)
        assert reg.get("greeter") is tool

    def test_get_nonexistent(self):
        reg = SkillRegistry()
        assert reg.get("nope") is None

    def test_unregister_existing(self):
        reg = SkillRegistry()
        tool = _make_fake_tool("x", "desc")
        reg.register(tool)
        assert reg.unregister("x") is True
        assert reg.get("x") is None

    def test_unregister_nonexistent(self):
        assert SkillRegistry().unregister("nope") is False

    def test_list_empty(self):
        assert SkillRegistry().list() == []

    def test_list_multiple(self):
        reg = SkillRegistry()
        t1 = _make_fake_tool("a", "1")
        t2 = _make_fake_tool("b", "2")
        reg.register(t1)
        reg.register(t2)
        assert len(reg.list()) == 2

    def test_clear(self):
        reg = SkillRegistry()
        reg.register(_make_fake_tool("a", "1"))
        reg.register(_make_fake_tool("b", "2"))
        reg.clear()
        assert len(reg) == 0

    def test_len(self):
        reg = SkillRegistry()
        assert len(reg) == 0
        reg.register(_make_fake_tool("x", "d"))
        assert len(reg) == 1

    def test_contains(self):
        reg = SkillRegistry()
        reg.register(_make_fake_tool("present", "d"))
        assert "present" in reg
        assert "absent" not in reg

    @pytest.mark.filterwarnings("ignore:Overwriting existing skill")
    def test_register_duplicate_warns(self):
        reg = SkillRegistry()
        reg.register(_make_fake_tool("dup", "first"))
        with pytest.warns(UserWarning, match="Overwriting existing skill 'dup'"):
            reg.register(_make_fake_tool("dup", "second"))

    def test_register_duplicate_overwrites(self):
        reg = SkillRegistry()
        t1 = _make_fake_tool("dup", "first")
        t2 = _make_fake_tool("dup", "second")
        reg.register(t1)
        with pytest.warns(UserWarning):
            reg.register(t2)
        assert reg.get("dup") is t2  # last wins

    def test_repr(self):
        reg = SkillRegistry()
        reg.register(_make_fake_tool("a", "1"))
        r = repr(reg)
        assert "SkillRegistry" in r
        assert "a" in r


# ============================================================================
#  FIND SKILLS IN DIR / LOAD SKILL
# ============================================================================


class TestFindSkillsInDir:
    def test_returns_empty_for_nonexistent_dir(self):
        assert _find_skills_in_dir(Path("/tmp/__axiom_nonexistent__")) == []

    def test_returns_empty_for_file(self, tmp_path):
        f = tmp_path / "not_a_dir.txt"
        f.write_text("x")
        assert _find_skills_in_dir(f) == []

    def test_finds_py_file(self, tmp_path):
        skill_file = tmp_path / "my_skill.py"
        skill_file.write_text("# a skill")
        found = _find_skills_in_dir(tmp_path)
        assert skill_file in found

    def test_skips_init_py(self, tmp_path):
        (tmp_path / "__init__.py").write_text("")
        found = _find_skills_in_dir(tmp_path)
        assert not any(p.name == "__init__.py" for p in found)

    def test_skips_hidden_files(self, tmp_path):
        (tmp_path / ".hidden.py").write_text("")
        found = _find_skills_in_dir(tmp_path)
        assert not any(p.name.startswith(".") for p in found)

    def test_finds_package_dir(self, tmp_path):
        pkg = tmp_path / "skill_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("# package skill")
        found = _find_skills_in_dir(tmp_path)
        assert pkg in found

    def test_skips_dir_without_init(self, tmp_path):
        empty_dir = tmp_path / "not_a_skill"
        empty_dir.mkdir()
        found = _find_skills_in_dir(tmp_path)
        assert empty_dir not in found


class TestLoadSkill:
    def test_load_valid_file_skill(self, tmp_path):
        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)
        tool = load_skill(skill_file)
        assert tool is not None
        assert tool.name == "hello"
        assert "Hello" in tool.execute(name="World")

    def test_load_from_nonexistent_path(self, tmp_path):
        assert load_skill(tmp_path / "nope.py") is None

    def test_load_file_missing_create_tool(self, tmp_path):
        f = tmp_path / "broken.py"
        f.write_text("x = 1\n")
        assert load_skill(f) is None

    def test_load_file_invalid_syntax(self, tmp_path):
        f = tmp_path / "bad_syntax.py"
        f.write_text("def broken(:")
        assert load_skill(f) is None

    def test_load_file_create_tool_returns_wrong_type(self, tmp_path):
        f = tmp_path / "bad_ret.py"
        f.write_text("def create_tool():\n    return 'not a tool'\n")
        assert load_skill(f) is None

    def test_load_package_skill(self, tmp_path):
        pkg = tmp_path / "greeter"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(VALID_SKILL_CODE)
        tool = load_skill(pkg)
        assert tool is not None
        assert tool.name == "hello"

    def test_load_from_dir_returns_none(self, tmp_path):
        """load_skill called on a dir that isn't a package returns None."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_skill(empty) is None


# ============================================================================
#  SKILL LOADER
# ============================================================================


class TestSkillLoader:
    def test_loader_creates_registry(self):
        loader = SkillLoader()
        assert loader.registry is not None
        assert len(loader.registry) == 0

    def test_loader_accepts_custom_registry(self):
        reg = SkillRegistry()
        loader = SkillLoader(registry=reg)
        assert loader.registry is reg

    def test_load_from_dir_populates_registry(self, tmp_path):
        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)
        loader = SkillLoader()
        tools = loader.load_from_dir(tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "hello"
        assert loader.registry.get("hello") is tools[0]

    def test_load_from_dir_skips_invalid_skills(self, tmp_path):
        (tmp_path / "good.py").write_text(VALID_SKILL_CODE)
        (tmp_path / "bad.py").write_text("x = 1\n")  # no create_tool
        loader = SkillLoader()
        tools = loader.load_from_dir(tmp_path)
        assert len(tools) == 1
        assert tools[0].name == "hello"

    def test_discover_returns_empty_when_no_builtin(self, monkeypatch):
        """If builtin dir can't be found, discover() returns empty list."""
        loader = SkillLoader()
        monkeypatch.setattr(loader, "_builtin_dir", lambda: None)
        monkeypatch.setattr(loader, "_user_skills_dir", lambda: None)
        monkeypatch.setattr(loader, "_load_entry_points", lambda: [])
        tools = loader.discover()
        assert tools == []

    def test_builtin_dir_detection(self):
        loader = SkillLoader()
        d = loader._builtin_dir()
        if d is not None:
            assert d.name == "builtin"
            assert d.is_dir()
            # Should contain at least the __init__.py
            assert (d / "__init__.py").exists()

    def test_user_skills_dir_none_when_not_exists(self):
        loader = SkillLoader()
        d = loader._user_skills_dir()
        assert d is None  # ~/.axiom/skills/ shouldn't exist in CI

    @pytest.mark.filterwarnings("ignore")
    def test_discover_loads_builtin_skills(self):
        """When the package is properly installed, discover() finds builtins."""
        loader = SkillLoader()
        tools = loader.discover()
        # This test works when run from the project root with the package
        # installed in development mode (pip install -e .)
        builtin = loader._builtin_dir()
        if builtin is not None:
            assert len(tools) >= 1
            names = {t.name for t in tools}
            assert "url_fetch" in names or "json" in names or "file_stats" in names


# ============================================================================
#  BUILT-IN SKILLS
# ============================================================================


class TestBuiltinSkills:
    """Verify each built-in skill is structurally valid and works."""

    @pytest.fixture
    def builtin_tools(self):
        loader = SkillLoader()
        d = loader._builtin_dir()
        if d is None:
            pytest.skip("Builtin dir not accessible in test context")
        return loader.load_from_dir(d)

    def test_all_have_valid_schema(self, builtin_tools):
        for t in builtin_tools:
            s = t.schema()
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "parameters" in s["function"]

    def test_all_have_unique_names(self, builtin_tools):
        names = [t.name for t in builtin_tools]
        assert len(names) == len(set(names))

    def test_url_fetch_schema(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "url_fetch"), None)
        if tool is None:
            pytest.skip("url_fetch not loaded")
        s = tool.schema()
        assert "url" in s["function"]["parameters"]["properties"]
        assert "url" in s["function"]["parameters"]["required"]

    def test_url_fetch_bad_url(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "url_fetch"), None)
        if tool is None:
            pytest.skip("url_fetch not loaded")
        result = tool.execute(url="http://nonexistent-domain-xyzabc.test/")
        assert "Error" in result

    def test_json_validate_valid(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "json"), None)
        if tool is None:
            pytest.skip("json not loaded")
        result = tool.execute(action="validate", json_string='{"a": 1}')
        assert "Valid JSON" in result

    def test_json_validate_invalid(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "json"), None)
        if tool is None:
            pytest.skip("json not loaded")
        result = tool.execute(action="validate", json_string="{bad json}")
        assert "Invalid JSON" in result

    def test_json_format(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "json"), None)
        if tool is None:
            pytest.skip("json not loaded")
        result = tool.execute(action="format", json_string='{"b":2,"a":1}')
        assert '"a"' in result
        assert '"b"' in result

    def test_json_query(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "json"), None)
        if tool is None:
            pytest.skip("json not loaded")
        result = tool.execute(
            action="query",
            json_string='{"data": {"items": [{"name": "test"}]}}',
            key_path="data.items[0].name",
        )
        assert "test" in result

    def test_json_query_missing_key(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "json"), None)
        if tool is None:
            pytest.skip("json not loaded")
        result = tool.execute(
            action="query",
            json_string='{"a": 1}',
            key_path="b",
        )
        assert "not found" in result

    def test_file_stats_file(self, builtin_tools, tmp_path):
        tool = next((t for t in builtin_tools if t.name == "file_stats"), None)
        if tool is None:
            pytest.skip("file_stats not loaded")
        f = tmp_path / "hello.txt"
        f.write_text("line1\nline2\nline3\n")
        result = tool.execute(path=str(f))
        assert "Lines:" in result
        assert "3" in result

    def test_file_stats_dir(self, builtin_tools, tmp_path):
        tool = next((t for t in builtin_tools if t.name == "file_stats"), None)
        if tool is None:
            pytest.skip("file_stats not loaded")
        (tmp_path / "a.py").write_text("def f(): pass\n")
        (tmp_path / "b.py").write_text("x = 1\n")
        result = tool.execute(path=str(tmp_path))
        assert "Files:" in result or "files" in result
        assert ".py" in result

    def test_file_stats_not_found(self, builtin_tools):
        tool = next((t for t in builtin_tools if t.name == "file_stats"), None)
        if tool is None:
            pytest.skip("file_stats not loaded")
        result = tool.execute(path="/tmp/axiom__nonexistent__")
        assert "not found" in result.lower() or "Error" in result


# ============================================================================
#  MANAGER
# ============================================================================


class TestSkillManager:
    def test_creates_skills_dir(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        mgr = SkillManager()
        assert mgr.skills_dir.is_dir()

    def test_install_from_path_py_file(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)

        mgr = SkillManager()
        tool = mgr.install_from_path(str(skill_file))
        assert tool is not None
        assert tool.name == "hello"

        # File should be copied to skills dir
        dest = mgr.skills_dir / "hello.py"
        assert dest.exists()

    def test_install_twice_raises(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)

        mgr = SkillManager()
        mgr.install_from_path(str(skill_file))
        with pytest.raises(SkillError, match="already exists"):
            mgr.install_from_path(str(skill_file))

    def test_remove_existing(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)

        mgr = SkillManager()
        mgr.install_from_path(str(skill_file))
        assert mgr.remove("hello") is True
        assert not (mgr.skills_dir / "hello.py").exists()
        assert mgr.registry.get("hello") is None

    def test_remove_nonexistent(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        mgr = SkillManager()
        assert mgr.remove("nope") is False

    def test_install_from_nonexistent_path(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        mgr = SkillManager()
        with pytest.raises(SkillError, match="does not exist"):
            mgr.install_from_path("/nonexistent/path/skill.py")

    def test_list_installed_empty(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        mgr = SkillManager()
        assert mgr.list_installed() == []

    def test_list_installed_with_skill(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)

        mgr = SkillManager()
        mgr.install_from_path(str(skill_file))
        listing = mgr.list_installed()
        assert len(listing) == 1
        assert listing[0]["name"] == "hello"
        assert "description" in listing[0]

    def test_install_package_skill(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        pkg = tmp_path / "greeter"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(VALID_SKILL_CODE)

        mgr = SkillManager()
        tool = mgr.install_from_path(str(pkg))
        assert tool is not None
        assert (mgr.skills_dir / "greeter").is_dir()


# ============================================================================
#  GENERATOR — validate_skill
# ============================================================================


class TestValidateSkill:
    def test_valid_skill(self):
        result = validate_skill(VALID_SKILL_CODE)
        assert result.valid is True
        assert result.errors == []

    def test_invalid_syntax(self):
        result = validate_skill("def broken(:")
        assert result.valid is False
        assert any("Syntax" in e for e in result.errors)

    def test_missing_create_tool(self):
        code = "x = 1\n"
        result = validate_skill(code)
        assert result.valid is False
        assert any("create_tool" in e for e in result.errors)

    def test_empty_code(self):
        result = validate_skill("")
        assert result.valid is False

    def test_dangerous_eval_warning(self):
        code = """
from axiom.tools.base import Tool

class DangerTool(Tool):
    name = "danger"
    description = "dangerous"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    def execute(self, x: str) -> str:
        return eval(x)

def create_tool() -> Tool:
    return DangerTool()
"""
        result = validate_skill(code)
        # Should still be valid (warnings don't fail)
        assert result.valid is True
        assert any("eval" in w for w in result.warnings)

    def test_dangerous_import_warning(self):
        code = """
import pickle
from axiom.tools.base import Tool

class PickleTool(Tool):
    name = "pickle_tool"
    description = "unsafe"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    def execute(self, x: str) -> str:
        return "ok"

def create_tool() -> Tool:
    return PickleTool()
"""
        result = validate_skill(code)
        assert result.valid is True
        assert any("pickle" in w for w in result.warnings)

    def test_create_tool_returns_none_fails(self):
        code = """
from axiom.tools.base import Tool

def create_tool() -> Tool:
    return None
"""
        result = validate_skill(code)
        assert result.valid is False
        assert any("None" in e for e in result.errors)

    def test_tool_missing_attributes_fails(self):
        code = """
from axiom.tools.base import Tool

class IncompleteTool(Tool):
    name = "inc"
    description = "incomplete"
    parameters = {"type": "object", "properties": {}, "required": []}

def create_tool() -> Tool:
    return IncompleteTool()
"""
        result = validate_skill(code)
        assert result.valid is False


# ============================================================================
#  GENERATOR — generate_skill
# ============================================================================


class TestGenerateSkill:
    def test_generated_code_is_valid(self):
        code = generate_skill(
            name="greeter",
            description="Says hello",
            properties={"name": {"type": "string"}},
            required=["name"],
            body='return f"Hello, {name}!"',
        )
        # Validate the generated code
        result = validate_skill(code)
        assert result.valid is True, result.errors

    def test_generated_code_executes(self):
        code = generate_skill(
            name="greeter",
            description="Says hello",
            properties={"name": {"type": "string"}},
            required=["name"],
            body='return f"Hi, {name}!"',
        )
        result = validate_skill(code)
        assert result.valid is True

    def test_generated_skill_loads(self, tmp_path):
        code = generate_skill(
            name="test_gen",
            description="Generated test skill",
            properties={"msg": {"type": "string"}},
            required=["msg"],
            body="return f'Got: {msg}'",
        )
        f = tmp_path / "test_gen.py"
        f.write_text(code)
        tool = load_skill(f)
        assert tool is not None
        assert tool.name == "test_gen"
        result = tool.execute(msg="hello")
        assert "Got: hello" in result

    def test_default_body(self):
        code = generate_skill(
            name="defaults",
            description="Default skill",
        )
        assert "def execute(self, name)" in code
        assert "name" in code

    def test_class_name_convention(self):
        code = generate_skill(
            name="url_fetch",
            description="Fetch URLs",
        )
        assert "class UrlFetchTool(Tool)" in code

    def test_kebab_name(self):
        code = generate_skill(
            name="my-skill",
            description="Kebab case",
        )
        assert "class MySkillTool(Tool)" in code


# ============================================================================
#  INTEGRATION — skill tools can be used alongside ALL_TOOLS
# ============================================================================


class TestIntegration:
    def test_loaded_skill_can_be_merged_with_all_tools(self, tmp_path):
        """Skills produce Tools, and ALL_TOOLS expect Tools — they're compatible."""
        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)
        tool = load_skill(skill_file)
        assert tool is not None

        # Can be merged with existing tools
        combined = ALL_TOOLS + [tool]
        assert len(combined) == len(ALL_TOOLS) + 1
        assert any(t.name == "hello" for t in combined)

    def test_skill_tool_has_same_interface_as_builtin_tools(self, tmp_path):
        """Skills implement the same Tool ABC as built-in tools."""
        skill_file = tmp_path / "hello.py"
        skill_file.write_text(VALID_SKILL_CODE)
        tool = load_skill(skill_file)
        assert tool is not None

        s = tool.schema()
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert "parameters" in s["function"]

        # Can be looked up by name just like a built-in tool
        assert tool.name == "hello"

    def test_discover_and_agent_compatible(self):
        """SkillLoader.discover() returns list[Tool], same type Agent expects."""
        loader = SkillLoader()
        builtin_dir = loader._builtin_dir()
        if builtin_dir is None:
            pytest.skip("Builtin dir not accessible")

        tools = loader.load_from_dir(builtin_dir)
        # All returned items must be Tool instances
        for t in tools:
            assert isinstance(t, Tool)
            assert hasattr(t, "execute")
            assert hasattr(t, "schema")


# ============================================================================
#  HELPERS
# ============================================================================


def _make_fake_tool(name: str, description: str) -> Tool:
    """Create a minimal Tool subclass for testing.

    Uses ``type()`` instead of a local class to avoid class-body
    closure scoping issues with ``name = name``.
    """
    return type(
        "FakeTool",
        (Tool,),
        {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
            "execute": lambda self, x="": f"{name} executed with {x}",
        },
    )()
