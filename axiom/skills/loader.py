"""Skill loader — discovers and loads skills from multiple sources.

Sources (in priority order, later wins on name collision):

1. **Built-in** — shipped with Axiom in ``axiom/skills/builtin/``
2. **User** — installed at ``~/.axiom/skills/``
3. **Entry-points** — pip packages that register ``axiom.skills`` entry-points

Each skill is a Python file or package that exports ``create_tool() -> Tool``.
"""

import importlib
import importlib.metadata
import importlib.util
import sys
from pathlib import Path

from axiom.tools.base import Tool

from .registry import SkillRegistry

# ---------------------------------------------------------------------------
#  Low-level helpers
# ---------------------------------------------------------------------------


def _find_skills_in_dir(directory: Path) -> list[Path]:
    """Return all skill candidates found under *directory*.

    A candidate is either:
    * a ``.py`` file (excluding ``__init__.py`` / private helpers)
    * a sub-directory that contains an ``__init__.py``
    """
    if not directory.is_dir():
        return []

    skills: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith("_") or entry.name.startswith("."):
            continue
        if entry.suffix == ".py":
            skills.append(entry)
        elif entry.is_dir() and (entry / "__init__.py").exists():
            skills.append(entry)
    return skills


def _load_skill_from_file(filepath: Path) -> Tool | None:
    """Import a single-file skill and return its Tool (or None on failure)."""
    try:
        spec = importlib.util.spec_from_file_location(filepath.stem, filepath)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)

        # Temporarily add the parent directory so the skill can use
        # relative imports if it needs helper modules nearby.
        parent = str(filepath.parent.resolve())
        added = parent not in sys.path
        if added:
            sys.path.insert(0, parent)
        try:
            spec.loader.exec_module(module)
        finally:
            if added:
                sys.path.remove(parent)

        factory = getattr(module, "create_tool", None)
        if not callable(factory):
            return None

        tool = factory()
        return tool if isinstance(tool, Tool) else None
    except Exception:
        return None


def _load_skill_from_package(package_dir: Path) -> Tool | None:
    """Load a package-based skill (directory with ``__init__.py``)."""
    return _load_skill_from_file(package_dir / "__init__.py")


def load_skill(path: Path) -> Tool | None:
    """Load a single skill from a file or a package directory.

    Returns ``None`` (no exception) when the path doesn't contain a
    valid skill — this lets callers iterate over candidates safely.
    """
    if path.is_file() and path.suffix == ".py":
        return _load_skill_from_file(path)
    if path.is_dir():
        return _load_skill_from_package(path)
    return None


# ---------------------------------------------------------------------------
#  SkillLoader
# ---------------------------------------------------------------------------


class SkillLoader:
    """Discovers, loads and registers skills from all available sources.

    Usage::

        loader = SkillLoader()
        tools = loader.discover()          # list[Tool]
        registry = loader.registry          # SkillRegistry
    """

    def __init__(self, registry: SkillRegistry | None = None):
        self.registry = registry if registry is not None else SkillRegistry()

    # -- public API -----------------------------------------------------------

    def discover(self) -> list[Tool]:
        """Load skills from built-in, user and entry-point sources.

        Later sources silently overwrite earlier ones when names collide,
        which lets users override built-in skills.
        """
        tools: list[Tool] = []

        # 1. Built-in skills (shipped with Axiom)
        builtin = self._builtin_dir()
        if builtin is not None:
            tools.extend(self.load_from_dir(builtin))

        # 2. User-installed skills (~/.axiom/skills/)
        user = self._user_skills_dir()
        if user is not None:
            tools.extend(self.load_from_dir(user))

        # 3. Pip-installed skills (entry-point group axiom.skills)
        tools.extend(self._load_entry_points())

        return tools

    def load_from_dir(self, directory: Path) -> list[Tool]:
        """Scan *directory* and load every skill found inside."""
        loaded: list[Tool] = []
        for skill_path in _find_skills_in_dir(directory):
            tool = load_skill(skill_path)
            if tool is not None:
                self.registry.register(tool, source=str(skill_path))
                loaded.append(tool)
        return loaded

    # -- source helpers -------------------------------------------------------

    def _load_entry_points(self) -> list[Tool]:
        """Load skills registered via pip entry-points ``axiom.skills``."""
        loaded: list[Tool] = []
        try:
            for ep in importlib.metadata.entry_points(group="axiom.skills"):
                try:
                    factory = ep.load()
                    if not callable(factory):
                        continue
                    tool = factory()
                    if isinstance(tool, Tool):
                        self.registry.register(tool, source=f"ep:{ep.name}")
                        loaded.append(tool)
                except Exception:
                    continue
        except TypeError:
            # Python < 3.12 raises TypeError when group is not found
            pass
        return loaded

    # -- paths ----------------------------------------------------------------

    @staticmethod
    def _builtin_dir() -> Path | None:
        """Return the path to the built-in skills directory, or ``None``."""
        # loader.py lives at axiom/skills/loader.py
        # builtins are at axiom/skills/builtin/
        here = Path(__file__).resolve().parent
        builtin = here / "builtin"
        return builtin if builtin.is_dir() else None

    @staticmethod
    def _user_skills_dir() -> Path | None:
        """Return the path to ``~/.axiom/skills/``, or ``None``."""
        d = Path.home() / ".axiom" / "skills"
        return d if d.is_dir() else None
