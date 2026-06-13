"""Skill registry — maps skill names to Tool instances.

This is a simple in-memory registry. The SkillLoader populates it,
Skills consume it when building their tool list.
"""

import warnings

from axiom.tools.base import Tool


class SkillRegistry:
    """Registry for dynamically loaded skills (Tools)."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, source: str = "") -> None:
        """Register a tool under its ``.name``.

        Overwrites if a tool with the same name already exists and
        emits a warning so the caller knows a collision occurred.
        """
        existing = self._tools.get(tool.name)
        if existing is not None:
            src_hint = f" (from {existing.__class__.__module__})"
            warnings.warn(
                f"Overwriting existing skill '{tool.name}'{src_hint}",
                stacklevel=2,
            )
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a skill by name.  Returns ``True`` if it existed."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Tool | None:
        """Look up a skill by name."""
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        """Return all registered tools (order not guaranteed)."""
        return list(self._tools.values())

    def clear(self) -> None:
        """Remove all registered skills."""
        self._tools.clear()

    # -- convenience protocols -------------------------------------------------
    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        return f"<SkillRegistry ({len(self)} tools): {', '.join(self._tools)}>"
