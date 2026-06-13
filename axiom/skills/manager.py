"""Skill manager — install, remove and inspect skills on the filesystem.

The user-skills directory is ``~/.axiom/skills/``.
"""

import shutil
import subprocess
from pathlib import Path

from axiom.tools.base import Tool

from .loader import _find_skills_in_dir, load_skill
from .registry import SkillRegistry
from .spec import SkillError


class SkillManager:
    """Manage user-installed skills on disk.

    All operations happen inside ``~/.axiom/skills/``.
    """

    def __init__(self, registry: SkillRegistry | None = None):
        self.skills_dir = Path.home() / ".axiom" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry or SkillRegistry()

    # -- install --------------------------------------------------------------

    def install_from_path(self, source: str) -> Tool | None:
        """Copy a skill from a local path into the user skills directory.

        *source* can be a ``.py`` file or a directory.
        Returns the loaded Tool, or ``None`` on failure.
        """
        src = Path(source).expanduser().resolve()
        if not src.exists():
            raise SkillError(f"Path does not exist: {source}")

        if src.is_file() and src.suffix == ".py":
            dest = self.skills_dir / src.name
            # avoid overwriting without warning
            if dest.exists():
                raise SkillError(
                    f"Skill '{dest.stem}' already exists at {dest}. "
                    f"Remove it first or use a different name."
                )
            shutil.copy2(src, dest)
        elif src.is_dir():
            dest = self.skills_dir / src.name
            if dest.exists():
                raise SkillError(f"Skill '{src.name}' already exists at {dest}.")
            shutil.copytree(src, dest)
        else:
            raise SkillError(f"Unsupported source type: {source}")

        # Load and register the newly installed skill
        tool = load_skill(dest)
        if tool is not None:
            self.registry.register(tool, source=str(dest))
        return tool

    def install_from_git(self, url: str, name: str | None = None) -> Tool | None:
        """Clone a git repository and install it as a skill.

        The repo is cloned into ``~/.axiom/skills/{name}/``.
        If *name* is ``None`` the repo's directory name is used.
        """
        dest_parent = self.skills_dir
        # We clone into a temp name first to avoid partial clones
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            clone_path = Path(tmp) / "repo"
            try:
                subprocess.run(
                    ["git", "clone", url, str(clone_path)],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=120,
                )
            except subprocess.CalledProcessError as e:
                raise SkillError(f"Git clone failed:\n{e.stderr}") from e
            except FileNotFoundError as e:
                raise SkillError("git is not installed") from e

            # Determine skill name
            skill_name = name or clone_path.name

            # Find the skill inside the cloned repo
            candidates = _find_skills_in_dir(clone_path)
            if not candidates:
                # Maybe the whole repo IS the skill
                if (clone_path / "__init__.py").exists():
                    candidates = [clone_path]
                else:
                    raise SkillError(
                        f"No skill found in cloned repository. "
                        f"Expected a .py file or a package with __init__.py"
                    )

            # Install each candidate
            installed: list[Tool] = []
            for candidate in candidates:
                dest = dest_parent / (
                    candidate.name if candidate != clone_path else skill_name
                )
                if candidate.is_file():
                    shutil.copy2(candidate, dest)
                else:
                    shutil.copytree(candidate, dest)
                tool = load_skill(dest)
                if tool is not None:
                    self.registry.register(tool, source=str(dest))
                    installed.append(tool)

            return installed[0] if installed else None

    # -- remove ---------------------------------------------------------------

    def remove(self, name: str) -> bool:
        """Remove a skill by name from the user skills directory.

        Returns ``True`` if the skill existed and was removed.
        """
        path = self._find_skill_on_disk(name)
        if path is None:
            return False

        self.registry.unregister(name)

        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

        # Clean up any .pyc cache
        cache_dir = path.parent / "__pycache__"
        if cache_dir.is_dir():
            for cached in cache_dir.glob(f"{name}*"):
                cached.unlink()

        return True

    # -- list / inspect -------------------------------------------------------

    def list_installed(self) -> list[dict]:
        """Return metadata for every skill in the user directory."""
        results: list[dict] = []
        for skill_path in _find_skills_in_dir(self.skills_dir):
            results.append(self._inspect(skill_path))
        return results

    def _inspect(self, path: Path) -> dict:
        """Return metadata dict for a single skill path."""
        info = {
            "name": path.stem if path.suffix == ".py" else path.name,
            "path": str(path.resolve()),
            "type": "file" if path.is_file() else "package",
        }
        # Try to get a description from the registry
        tool = self.registry.get(info["name"])
        if tool is not None:
            info["description"] = tool.description
        return info

    def _find_skill_on_disk(self, name: str) -> Path | None:
        """Find a skill file or directory by name in ``self.skills_dir``."""
        # Check as a .py file
        py_path = self.skills_dir / f"{name}.py"
        if py_path.exists():
            return py_path
        # Check as a directory
        dir_path = self.skills_dir / name
        if dir_path.is_dir() and (dir_path / "__init__.py").exists():
            return dir_path
        return None
