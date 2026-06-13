"""AnalyzeTool — expose code analysis as an Agent tool.

Registers with the Tool registry so the LLM agent can perform structural
code analysis queries directly from its tool-calling interface.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..code_analysis import ProjectAnalyzer
from .base import Tool


class AnalyzeTool(Tool):
    name = "analyze"
    description = (
        "Analyze code structure using AST. "
        "Supports: function_list, call_graph, complexity, "
        "find_definition, find_usages, dependencies, refactor_rename."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "function_list",
                    "call_graph",
                    "complexity",
                    "find_definition",
                    "find_usages",
                    "dependencies",
                    "refactor_rename",
                ],
                "description": "The analysis action to perform",
            },
            "path": {
                "type": "string",
                "description": "File or directory to analyze",
            },
            "symbol": {
                "type": "string",
                "description": "Function/class name (for find_* actions)",
            },
            "new_name": {
                "type": "string",
                "description": "New name (for refactor_rename)",
            },
        },
        "required": ["action"],
    }

    def __init__(self):
        self._analyzer = ProjectAnalyzer()
        self._analyzed_path: str | None = None

    def execute(
        self,
        action: str,
        path: str = ".",
        symbol: str | None = None,
        new_name: str | None = None,
    ) -> str:
        """Execute an analysis action and return the result as text."""
        root = Path(path).resolve()

        # Ensure analysis is done (or re-done if path changed)
        if self._analyzed_path != str(root) or self._analyzer._result is None:
            if not root.exists():
                return f"Error: path not found: {path}"
            if not root.is_dir():
                return f"Error: expected a directory, got file: {path}"
            try:
                self._analyzer.analyze(root)
                self._analyzed_path = str(root)
            except Exception as e:
                return f"Error analyzing project: {e}"

        try:
            if action == "function_list":
                return self._function_list()
            elif action == "call_graph":
                return self._call_graph()
            elif action == "complexity":
                return self._complexity()
            elif action == "find_definition":
                if not symbol:
                    return "Error: 'symbol' is required for find_definition"
                return self._find_definition(symbol)
            elif action == "find_usages":
                if not symbol:
                    return "Error: 'symbol' is required for find_usages"
                return self._find_usages(symbol)
            elif action == "dependencies":
                return self._dependencies()
            elif action == "refactor_rename":
                if not symbol:
                    return "Error: 'symbol' (old_name) is required for refactor_rename"
                if not new_name:
                    return "Error: 'new_name' is required for refactor_rename"
                return self._refactor_rename(symbol, new_name, root)
            else:
                return f"Error: unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"

    def _function_list(self) -> str:
        result = self._analyzer._result
        if not result:
            return "No analysis result available"

        lines = ["Functions:"]
        for f in sorted(result.functions, key=lambda x: (x.file, x.line_start)):
            lines.append(
                f"  {f.qualified_name} ({f.file}:{f.line_start}) "
                f"complexity={f.complexity}"
            )
        for cls in sorted(result.classes, key=lambda x: (x.file, x.line_start)):
            lines.append(f"\nClass {cls.name} ({cls.file}:{cls.line_start}):")
            for m in cls.methods:
                lines.append(
                    f"  {m.qualified_name} ({m.file}:{m.line_start}) "
                    f"complexity={m.complexity}"
                )
        return "\n".join(lines)

    def _call_graph(self) -> str:
        result = self._analyzer._result
        if not result:
            return "No analysis result available"

        edges = result.call_graph.edges()
        if not edges:
            return "No call relationships found."

        lines = ["Call graph (caller -> callee):"]
        for caller, callee in edges[:100]:
            lines.append(f"  {caller} -> {callee}")
        if len(edges) > 100:
            lines.append(f"  ... ({len(edges) - 100} more edges)")

        # Also output DOT format
        lines.append("\nDOT format:")
        lines.append(result.call_graph.to_dot())
        return "\n".join(lines)

    def _complexity(self) -> str:
        result = self._analyzer._result
        if not result:
            return "No analysis result available"

        all_funcs = list(result.functions)
        for cls in result.classes:
            all_funcs.extend(cls.methods)

        if not all_funcs:
            return "No functions found."

        # Sort by complexity descending
        sorted_funcs = sorted(all_funcs, key=lambda x: x.complexity, reverse=True)

        lines = ["Complexity report (sorted by cyclomatic complexity):"]
        lines.append(f"{'Function':<40} {'McCabe':>8} {'Cognitive':>10} {'File'}")
        lines.append("-" * 80)
        for f in sorted_funcs:
            lines.append(
                f"{f.qualified_name:<40} {f.complexity:>8} {f.cognitive_complexity:>10} "
                f"{f.file}:{f.line_start}"
            )

        # Hotspots
        hotspots = result.complexity_hotspots(threshold=10)
        if hotspots:
            lines.append(f"\n⚠️ Complexity hotspots (>= 10): {len(hotspots)}")

        return "\n".join(lines)

    def _find_definition(self, symbol: str) -> str:
        info = self._analyzer.find_definition(symbol)
        if info is None:
            return f"Symbol '{symbol}' not found."

        if hasattr(info, "qualified_name"):
            # FunctionInfo
            return (
                f"Definition: {info.qualified_name}\n"
                f"  File: {info.file}:{info.line_start}-{info.line_end}\n"
                f"  Complexity: {info.complexity} (McCabe), "
                f"{info.cognitive_complexity} (cognitive)\n"
                f"  Calls: {', '.join(info.dependencies) if info.dependencies else 'none'}\n"
                f"  Called by: {', '.join(info.called_by) if info.called_by else 'none'}\n"
                f"  Docstring: {info.docstring or '(none)'}"
            )
        else:
            # ClassInfo
            return (
                f"Definition: {info.name} (class)\n"
                f"  File: {info.file}:{info.line_start}-{info.line_end}\n"
                f"  Bases: {', '.join(info.bases) if info.bases else 'none'}\n"
                f"  Methods: {', '.join(m.name for m in info.methods) or 'none'}\n"
                f"  Docstring: {info.docstring or '(none)'}"
            )

    def _find_usages(self, symbol: str) -> str:
        usages = self._analyzer.find_usages(symbol)
        if not usages:
            return f"No usages found for '{symbol}'."

        lines = [f"Usages of '{symbol}':"]
        for usage in usages:
            if hasattr(usage, "qualified_name"):
                lines.append(
                    f"  {usage.qualified_name} ({usage.file}:{usage.line_start})"
                )
            elif hasattr(usage, "module"):
                # ImportInfo
                lines.append(f"  import in {usage.file}:{usage.line}")
        return "\n".join(lines)

    def _dependencies(self) -> str:
        result = self._analyzer._result
        if not result:
            return "No analysis result available"

        modules = result.dependency_graph.modules()
        if not modules:
            return "No module dependencies found."

        lines = ["Module dependencies:"]
        for mod in modules:
            deps = result.dependency_graph.dependencies(mod)
            if deps:
                lines.append(f"  {mod} -> {', '.join(deps)}")

        cycles = result.dependency_graph.find_cycles()
        if cycles:
            lines.append(f"\n⚠️ Circular dependencies: {len(cycles)}")
            for cycle in cycles:
                lines.append(f"  {' -> '.join(cycle)}")

        # DOT format
        lines.append("\nDOT format:")
        lines.append(result.dependency_graph.to_dot())
        return "\n".join(lines)

    def _refactor_rename(self, old_name: str, new_name: str, root: Path) -> str:
        from ..code_analysis.refactor import refactor_rename

        result = refactor_rename(old_name, new_name, root, dry_run=True)
        lines = [
            f"Refactor rename: '{old_name}' -> '{new_name}'",
            f"Safety: {result.safety.value}",
        ]
        if result.warnings:
            lines.append("Warnings:")
            for w in result.warnings:
                lines.append(f"  ⚠ {w}")
        if result.changes:
            lines.append(f"Files to modify: {len(result.changes)}")
            for change in result.changes:
                lines.append(f"  {change.file} ({change.replacements} replacements)")
        else:
            lines.append("No changes needed.")
        return "\n".join(lines)
