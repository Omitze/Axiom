"""Call graph construction — who-calls-whom analysis.

Builds a directed graph of function calls and supports shortest-path
queries using BFS. Uses networkx if available, otherwise falls back to
a pure-Python adjacency list implementation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .ast_parser import ClassInfo, FunctionInfo


class CallGraph:
    """Directed graph of function call relationships.

    Nodes are function qualified names (e.g. ``"main"``, ``"MyClass.run"``).
    Edges represent "calls" relationships.

    Usage::

        cg = CallGraph()
        cg.build(functions, classes)
        path = cg.shortest_path("main", "process_data")
        callers = cg.callers("process_data")
        callees = cg.callees("main")
    """

    def __init__(self):
        # Adjacency list: node -> set of nodes it calls
        self._forward: dict[str, set[str]] = {}
        # Reverse adjacency: node -> set of nodes that call it
        self._reverse: dict[str, set[str]] = {}

    def build(
        self,
        functions: list[FunctionInfo],
        classes: list[ClassInfo],
    ) -> None:
        """Build the call graph from extracted function and class info.

        Populates forward edges (caller -> callee) and reverse edges
        (callee <- caller), and also fills in the ``called_by`` field
        of FunctionInfo objects.
        """
        # Register all known function names
        all_names: set[str] = set()
        for func in functions:
            all_names.add(func.qualified_name)
        for cls in classes:
            all_names.add(cls.name)
            for method in cls.methods:
                all_names.add(method.qualified_name)

        # Build edges
        for func in functions:
            caller = func.qualified_name
            self._ensure_node(caller)
            for dep in func.dependencies:
                # Try to resolve: first as qualified, then as simple name
                callee = self._resolve(dep, caller, all_names)
                if callee:
                    self._add_edge(caller, callee)

        for cls in classes:
            for method in cls.methods:
                caller = method.qualified_name
                self._ensure_node(caller)
                for dep in method.dependencies:
                    callee = self._resolve(dep, caller, all_names)
                    if callee:
                        self._add_edge(caller, callee)

        # Populate called_by for each function
        caller_map: dict[str, list[str]] = {}
        for caller, callees in self._forward.items():
            for callee in callees:
                caller_map.setdefault(callee, []).append(caller)

        for func in functions:
            func.called_by = caller_map.get(func.qualified_name, [])
        for cls in classes:
            for method in cls.methods:
                method.called_by = caller_map.get(method.qualified_name, [])

    def _resolve(self, dep: str, caller: str, all_names: set[str]) -> str | None:
        """Resolve a dependency name to a known function name.

        Tries:
        1. Exact match with *dep*
        2. If *dep* is a simple name and caller has a class, try Class.dep
        3. Search all names for a suffix match
        """
        if dep in all_names:
            return dep

        # If caller is a method (e.g. "MyClass.run"), try "MyClass.dep"
        if "." in caller:
            class_part = caller.rsplit(".", 1)[0]
            qualified = f"{class_part}.{dep}"
            if qualified in all_names:
                return qualified

        # Try matching by simple name (last part after dot)
        for name in all_names:
            if name.endswith(f".{dep}") or name == dep:
                return name

        return None

    def _ensure_node(self, name: str) -> None:
        if name not in self._forward:
            self._forward[name] = set()
        if name not in self._reverse:
            self._reverse[name] = set()

    def _add_edge(self, caller: str, callee: str) -> None:
        self._forward.setdefault(caller, set()).add(callee)
        self._reverse.setdefault(callee, set()).add(caller)

    # ------------------------------------------------------------------
    #  Query API
    # ------------------------------------------------------------------

    def shortest_path(self, from_func: str, to_func: str) -> list[str] | None:
        """Find the shortest call path between two functions using BFS.

        Returns an ordered list of function names from *from_func* to
        *to_func*, or None if no path exists.
        """
        if from_func not in self._forward:
            return None
        if to_func not in self._forward and to_func not in self._reverse:
            return None

        # BFS
        visited: set[str] = {from_func}
        queue: deque[tuple[str, list[str]]] = deque([(from_func, [from_func])])

        while queue:
            current, path = queue.popleft()
            if current == to_func:
                return path

            for neighbor in self._forward.get(current, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return None

    def callers(self, func: str) -> list[str]:
        """Return all functions that call *func*."""
        return sorted(self._reverse.get(func, set()))

    def callees(self, func: str) -> list[str]:
        """Return all functions called by *func*."""
        return sorted(self._forward.get(func, set()))

    def nodes(self) -> list[str]:
        """Return all function names in the graph."""
        return sorted(self._forward.keys())

    def edges(self) -> list[tuple[str, str]]:
        """Return all (caller, callee) edges."""
        result = []
        for caller, callees in self._forward.items():
            for callee in sorted(callees):
                result.append((caller, callee))
        return result

    def has_node(self, name: str) -> bool:
        return name in self._forward

    def to_dot(self) -> str:
        """Export the call graph in DOT format for visualization.

        Requires no external dependencies — just writes text.
        """
        lines = ["digraph CallGraph {"]
        lines.append("  rankdir=LR;")
        lines.append("  node [shape=box, style=filled, fillcolor=lightyellow];")
        for caller, callees in sorted(self._forward.items()):
            for callee in sorted(callees):
                lines.append(f'  "{caller}" -> "{callee}";')
        lines.append("}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "nodes": self.nodes(),
            "edges": self.edges(),
        }
