"""Analysis result container and report formatter.

Holds the complete analysis output and provides methods to generate
human-readable reports in JSON or Markdown format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .ast_parser import ClassInfo, FunctionInfo, ImportInfo
from .call_graph import CallGraph
from .dependency_graph import DependencyGraph


@dataclass
class AnalysisResult:
    """Complete result of project analysis."""

    root: str
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    call_graph: CallGraph = field(default_factory=CallGraph)
    dependency_graph: DependencyGraph = field(default_factory=DependencyGraph)

    # ------------------------------------------------------------------
    #  Summary & statistics
    # ------------------------------------------------------------------

    @property
    def total_functions(self) -> int:
        """Total number of top-level functions and class methods."""
        count = len(self.functions)
        for cls in self.classes:
            count += len(cls.methods)
        return count

    @property
    def total_classes(self) -> int:
        return len(self.classes)

    @property
    def total_files(self) -> int:
        """Number of distinct Python files."""
        files = set()
        for f in self.functions:
            files.add(f.file)
        for c in self.classes:
            files.add(c.file)
        for i in self.imports:
            files.add(i.file)
        return len(files)

    @property
    def total_lines(self) -> int:
        """Total lines of code across all functions and classes."""
        lines = 0
        for f in self.functions:
            lines += f.loc
        for c in self.classes:
            lines += c.loc
        return lines

    def summary(self) -> dict:
        """Return a high-level summary dict."""
        return {
            "root": self.root,
            "total_files": self.total_files,
            "total_functions": self.total_functions,
            "total_classes": self.total_classes,
            "total_lines": self.total_lines,
            "avg_complexity": self._avg_complexity(),
            "call_graph_nodes": len(self.call_graph.nodes()),
            "call_graph_edges": len(self.call_graph.edges()),
            "dependency_modules": len(self.dependency_graph.modules()),
        }

    def complexity_hotspots(self, threshold: int = 10) -> list[FunctionInfo]:
        """Return functions with cyclomatic complexity above *threshold*."""
        all_funcs = list(self.functions)
        for cls in self.classes:
            all_funcs.extend(cls.methods)
        return [f for f in all_funcs if f.complexity >= threshold]

    def _avg_complexity(self) -> float:
        """Average cyclomatic complexity across all functions."""
        all_funcs = list(self.functions)
        for cls in self.classes:
            all_funcs.extend(cls.methods)
        if not all_funcs:
            return 0.0
        return round(sum(f.complexity for f in all_funcs) / len(all_funcs), 2)

    # ------------------------------------------------------------------
    #  Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "root": self.root,
            "summary": self.summary(),
            "functions": [f.to_dict() for f in self.functions],
            "classes": [c.to_dict() for c in self.classes],
            "imports": [i.to_dict() for i in self.imports],
            "call_graph": self.call_graph.to_dict(),
            "dependency_graph": self.dependency_graph.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        """Return a JSON string representation."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ======================================================================
#  Report formatting
# ======================================================================


def format_report(result: AnalysisResult, format: str = "markdown") -> str:
    """Generate a human-readable report from an AnalysisResult.

    Parameters
    ----------
    result:
        The analysis result to format.
    format:
        Output format: ``"markdown"`` or ``"json"``.

    Returns
    -------
    str
    """
    if format == "json":
        return result.to_json()
    elif format == "markdown":
        return _format_markdown(result)
    else:
        raise ValueError(f"Unknown format: {format!r}. Use 'markdown' or 'json'.")


def _format_markdown(result: AnalysisResult) -> str:
    """Generate a Markdown report."""
    lines: list[str] = []

    # Header
    lines.append(f"# Code Analysis Report")
    lines.append("")
    lines.append(f"**Project**: `{result.root}`")
    lines.append("")

    # Summary
    s = result.summary()
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Files | {s['total_files']} |")
    lines.append(f"| Functions | {s['total_functions']} |")
    lines.append(f"| Classes | {s['total_classes']} |")
    lines.append(f"| Total Lines | {s['total_lines']} |")
    lines.append(f"| Avg Complexity | {s['avg_complexity']} |")
    lines.append(f"| Call Graph Nodes | {s['call_graph_nodes']} |")
    lines.append(f"| Call Graph Edges | {s['call_graph_edges']} |")
    lines.append("")

    # Complexity hotspots
    hotspots = result.complexity_hotspots(threshold=10)
    if hotspots:
        lines.append("## Complexity Hotspots")
        lines.append("")
        lines.append("| Function | File | Complexity | Cognitive |")
        lines.append("|----------|------|------------|-----------|")
        for f in sorted(hotspots, key=lambda x: x.complexity, reverse=True):
            lines.append(
                f"| `{f.qualified_name}` | `{f.file}:{f.line_start}` "
                f"| {f.complexity} | {f.cognitive_complexity} |"
            )
        lines.append("")

    # Functions list
    if result.functions:
        lines.append("## Functions")
        lines.append("")
        for f in sorted(result.functions, key=lambda x: (x.file, x.line_start)):
            lines.append(
                f"- **`{f.qualified_name}`** "
                f"(`{f.file}:{f.line_start}`) "
                f"complexity={f.complexity} cognitive={f.cognitive_complexity}"
            )
        lines.append("")

    # Classes list
    if result.classes:
        lines.append("## Classes")
        lines.append("")
        for c in sorted(result.classes, key=lambda x: (x.file, x.line_start)):
            bases = f"({', '.join(c.bases)})" if c.bases else ""
            lines.append(
                f"- **`{c.name}{bases}`** "
                f"(`{c.file}:{c.line_start}`) "
                f"methods={len(c.methods)}"
            )
        lines.append("")

    # Call graph summary
    cg_edges = result.call_graph.edges()
    if cg_edges:
        lines.append("## Call Graph")
        lines.append("")
        lines.append("```")
        for caller, callee in cg_edges[:50]:  # limit display
            lines.append(f"  {caller} → {callee}")
        if len(cg_edges) > 50:
            lines.append(f"  ... ({len(cg_edges) - 50} more edges)")
        lines.append("```")
        lines.append("")

    # Dependency graph
    dep_modules = result.dependency_graph.modules()
    if dep_modules:
        cycles = result.dependency_graph.find_cycles()
        lines.append("## Dependencies")
        lines.append("")
        lines.append(f"Total modules: {len(dep_modules)}")
        if cycles:
            lines.append("")
            lines.append(f"⚠️ **Circular dependencies detected**: {len(cycles)}")
            for cycle in cycles:
                lines.append(f"  → {' → '.join(cycle)}")
        lines.append("")

    return "\n".join(lines)
