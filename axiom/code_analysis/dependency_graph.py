"""Module dependency graph — import relationship analysis.

Builds a directed graph of module-level dependencies based on import
statements, and provides reachability and cycle-detection queries.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .ast_parser import ImportInfo

_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
}


@dataclass
class ModuleInfo:
    """Metadata about a single Python module."""

    name: str  # dotted module name (e.g. "axiom.agent")
    file: str  # relative path (e.g. "axiom/agent.py")
    imports: list[str] = field(default_factory=list)  # modules it imports
    imported_by: list[str] = field(default_factory=list)  # modules that import it

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "imports": list(self.imports),
            "imported_by": list(self.imported_by),
        }


class DependencyGraph:
    """Directed graph of module-level dependencies.

    Nodes are dotted module names; edges point from importer to importee.

    Usage::

        dg = DependencyGraph()
        dg.build(Path("src"), imports)
        deps = dg.dependencies("axiom.agent")
        users = dg.dependents("axiom.agent")
        cycles = dg.find_cycles()
    """

    def __init__(self):
        self._modules: dict[str, ModuleInfo] = {}
        # Adjacency: module -> modules it depends on
        self._forward: dict[str, set[str]] = {}
        # Reverse: module -> modules that depend on it
        self._reverse: dict[str, set[str]] = {}

    def build(self, root: Path, imports: list[ImportInfo]) -> None:
        """Build the dependency graph from extracted import info.

        Parameters
        ----------
        root:
            Project root directory.
        imports:
            List of ImportInfo objects from ASTParser.
        """
        # Collect all modules from files
        self._discover_modules(root)

        # Build edges from import info
        for imp in imports:
            # The module doing the importing
            importer = self._file_to_module(imp.file)
            if not importer:
                continue

            self._ensure_node(importer)

            # Resolve the imported module
            if imp.is_from:
                # from X import Y → dependency on X
                imported = imp.module
            else:
                # import X → dependency on X
                imported = imp.module

            # Only track internal dependencies (within the project)
            if imported and self._is_internal(imported):
                self._add_edge(importer, imported)

        # Populate imported_by lists
        for mod_name, mod_info in self._modules.items():
            mod_info.imported_by = sorted(self._reverse.get(mod_name, set()))
            mod_info.imports = sorted(self._forward.get(mod_name, set()))

    def dependencies(self, module: str, transitive: bool = False) -> list[str]:
        """Return modules that *module* depends on.

        Parameters
        ----------
        module:
            Dotted module name.
        transitive:
            If True, include transitive dependencies (all reachable nodes).

        Returns
        -------
        list[str]
        """
        if not transitive:
            return sorted(self._forward.get(module, set()))

        # BFS for transitive deps
        visited: set[str] = set()
        queue: deque[str] = deque([module])
        while queue:
            current = queue.popleft()
            for dep in self._forward.get(current, set()):
                if dep not in visited:
                    visited.add(dep)
                    queue.append(dep)
        return sorted(visited)

    def dependents(self, module: str, transitive: bool = False) -> list[str]:
        """Return modules that depend on *module*.

        Parameters
        ----------
        module:
            Dotted module name.
        transitive:
            If True, include transitive dependents.

        Returns
        -------
        list[str]
        """
        if not transitive:
            return sorted(self._reverse.get(module, set()))

        visited: set[str] = set()
        queue: deque[str] = deque([module])
        while queue:
            current = queue.popleft()
            for dep in self._reverse.get(current, set()):
                if dep not in visited:
                    visited.add(dep)
                    queue.append(dep)
        return sorted(visited)

    def find_cycles(self) -> list[list[str]]:
        """Detect all circular dependencies using DFS.

        Returns a list of cycles, where each cycle is a list of module names.
        """
        cycles: list[list[str]] = []
        visited: set[str] = set()
        stack: list[str] = []

        def dfs(node: str):
            if node in stack:
                # Found a cycle
                cycle_start = stack.index(node)
                cycle = stack[cycle_start:] + [node]
                cycles.append(cycle)
                return
            if node in visited:
                return

            visited.add(node)
            stack.append(node)

            for neighbor in self._forward.get(node, set()):
                dfs(neighbor)

            stack.pop()

        for node in self._forward:
            if node not in visited:
                dfs(node)

        return cycles

    def topological_sort(self) -> list[str] | None:
        """Return modules in dependency order (topological sort).

        Returns None if there are cycles.
        """
        if self.find_cycles():
            return None

        # Kahn's algorithm
        in_degree: dict[str, int] = {n: 0 for n in self._forward}
        for _, deps in self._forward.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] = in_degree.get(dep, 0)

        for _, deps in self._forward.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        queue = deque([n for n, d in in_degree.items() if d == 0])
        result = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in self._forward.get(node, set()):
                if neighbor in in_degree:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

        return result if len(result) == len(in_degree) else None

    def modules(self) -> list[str]:
        """Return all module names."""
        return sorted(self._forward.keys())

    def get_module(self, name: str) -> ModuleInfo | None:
        """Get info about a specific module."""
        return self._modules.get(name)

    def to_dot(self) -> str:
        """Export the dependency graph in DOT format."""
        lines = ["digraph DependencyGraph {"]
        lines.append("  rankdir=LR;")
        lines.append("  node [shape=box, style=filled, fillcolor=lightblue];")
        for importer, importees in sorted(self._forward.items()):
            for importee in sorted(importees):
                lines.append(f'  "{importer}" -> "{importee}";')
        lines.append("}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "modules": {name: info.to_dict() for name, info in self._modules.items()},
            "edges": [
                {"from": a, "to": b}
                for a, deps in self._forward.items()
                for b in sorted(deps)
            ],
        }

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _discover_modules(self, root: Path) -> None:
        """Scan the project for all Python modules."""
        for py_file in root.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in py_file.parts):
                continue
            rel = str(py_file.relative_to(root)).replace("\\", "/")
            mod_name = self._file_to_module(rel)
            if mod_name:
                self._ensure_node(mod_name)
                self._modules[mod_name] = ModuleInfo(
                    name=mod_name,
                    file=rel,
                )

    def _file_to_module(self, file_path: str) -> str:
        """Convert a file path to a dotted module name.

        Examples::

            "axiom/agent.py"    → "axiom.agent"
            "axiom/tools/__init__.py" → "axiom.tools"
            "setup.py"             → "setup"
        """
        # Remove .py extension
        path = file_path
        if path.endswith("/__init__.py"):
            path = path[:-12]  # remove /__init__.py
        elif path.endswith(".py"):
            path = path[:-3]

        # Replace slashes with dots
        return path.replace("/", ".")

    def _is_internal(self, module: str) -> bool:
        """Check if a module name refers to a project-internal module."""
        return module in self._forward or any(
            m.startswith(module) for m in self._forward
        )

    def _ensure_node(self, name: str) -> None:
        if name not in self._forward:
            self._forward[name] = set()
        if name not in self._reverse:
            self._reverse[name] = set()

    def _add_edge(self, importer: str, importee: str) -> None:
        self._forward.setdefault(importer, set()).add(importee)
        self._reverse.setdefault(importee, set()).add(importer)
