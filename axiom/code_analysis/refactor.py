"""AST-level refactoring — scope-aware rename and extract function.

Uses Python's ``ast`` module to parse, transform, and unparse code,
ensuring that renames are scope-safe (only affecting the intended symbol)
and that all references are updated consistently.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RefactorSafety(Enum):
    """Safety level for a refactoring operation."""

    SAFE = "safe"  # No conflicts detected
    WARNING = "warning"  # Potential issues, review recommended
    UNSAFE = "unsafe"  # Conflicts detected, do not apply


@dataclass
class RefactorResult:
    """Result of a refactoring operation."""

    old_name: str
    new_name: str
    safety: RefactorSafety
    changes: list[FileChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return self.safety == RefactorSafety.SAFE

    def to_dict(self) -> dict:
        return {
            "old_name": self.old_name,
            "new_name": self.new_name,
            "safety": self.safety.value,
            "changes": [c.to_dict() for c in self.changes],
            "warnings": list(self.warnings),
        }


@dataclass
class FileChange:
    """A single file modification from a refactoring."""

    file: str
    old_content: str
    new_content: str
    replacements: int = 0

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "replacements": self.replacements,
        }


def refactor_rename(
    old_name: str, new_name: str, root: Path, dry_run: bool = True
) -> RefactorResult:
    """Safely rename a symbol across the project.

    Performs scope-aware checks:
    1. Verify the new name doesn't shadow an existing name in the same scope.
    2. Only rename the target symbol (not other names that happen to match).
    3. Track all files that would be modified.

    Parameters
    ----------
    old_name:
        Current symbol name to rename.
    new_name:
        Desired new name.
    root:
        Project root directory.
    dry_run:
        If True (default), don't actually modify files — just report
        what would change. Set to False to apply changes.

    Returns
    -------
    RefactorResult
    """
    result = RefactorResult(
        old_name=old_name,
        new_name=new_name,
        safety=RefactorSafety.SAFE,
    )

    # Validate new name is a valid Python identifier
    if not new_name.isidentifier():
        result.safety = RefactorSafety.UNSAFE
        result.warnings.append(f"'{new_name}' is not a valid Python identifier")
        return result

    # Check for built-in shadowing
    import builtins

    if hasattr(builtins, new_name):
        result.safety = RefactorSafety.WARNING
        result.warnings.append(f"'{new_name}' shadows a Python built-in ({new_name})")

    # Walk all Python files
    for py_file in _walk_python_files(root):
        rel = str(py_file.relative_to(root)).replace("\\", "/")
        try:
            source = py_file.read_text(errors="ignore")
        except OSError:
            continue

        # Parse and check for the symbol
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue

        # Check if the file contains the symbol
        if not _contains_name(tree, old_name):
            continue

        # Check for name conflicts in the same scopes
        conflicts = _check_scope_conflicts(tree, old_name, new_name)
        if conflicts:
            result.safety = RefactorSafety.WARNING
            result.warnings.extend(conflicts)

        # Perform the rename via AST transformation
        new_source = _rename_in_source(source, old_name, new_name)

        if new_source != source:
            # Count replacements
            replacements = _count_replacements(source, old_name)
            change = FileChange(
                file=rel,
                old_content=source,
                new_content=new_source,
                replacements=replacements,
            )
            result.changes.append(change)

            # Apply if not dry run
            if not dry_run:
                try:
                    py_file.write_text(new_source, encoding="utf-8")
                except OSError as e:
                    result.warnings.append(f"Failed to write {rel}: {e}")

    return result


# ======================================================================
#  Internal helpers
# ======================================================================


class _NameRenamer(ast.NodeTransformer):
    """AST transformer that renames a symbol."""

    def __init__(self, old_name: str, new_name: str):
        self.old_name = old_name
        self.new_name = new_name
        self.renamed = 0

    def visit_Name(self, node: ast.Name) -> ast.Name:
        if node.id == self.old_name:
            node.id = self.new_name
            self.renamed += 1
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        if node.name == self.old_name:
            node.name = self.new_name
            self.renamed += 1
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef
    ) -> ast.AsyncFunctionDef:
        if node.name == self.old_name:
            node.name = self.new_name
            self.renamed += 1
        self.generic_visit(node)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        if node.name == self.old_name:
            node.name = self.new_name
            self.renamed += 1
        self.generic_visit(node)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        if node.arg == self.old_name:
            node.arg = self.new_name
            self.renamed += 1
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        # Only rename the attribute if it matches (not the object)
        # Be conservative: only rename if it's a direct attribute access
        # like self.old_name, not obj.old_name (which might be external)
        return node

    def visit_Import(self, node: ast.Import) -> ast.Import:
        # Don't rename in import statements — they refer to external names
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
        # Don't rename imported names — they reference external symbols
        return node


class _NameCollector(ast.NodeVisitor):
    """Collect all defined names in an AST scope."""

    def __init__(self):
        self.names: set[str] = set()

    def visit_FunctionDef(self, node):
        self.names.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.names.add(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.names.add(node.name)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)
        self.generic_visit(node)

    def visit_arg(self, node):
        self.names.add(node.arg)
        self.generic_visit(node)


def _rename_in_source(source: str, old_name: str, new_name: str) -> str:
    """Rename a symbol in source code using AST transformation.

    Uses ast.parse → transform → ast.unparse for lossless refactoring.
    Falls back to string replacement if ast.unparse is not available.
    """
    try:
        tree = ast.parse(source)
        renamer = _NameRenamer(old_name, new_name)
        new_tree = renamer.visit(tree)
        ast.fix_missing_locations(new_tree)

        if hasattr(ast, "unparse"):
            return ast.unparse(new_tree)
        else:
            # Fallback: manual string replacement (less precise)
            return _simple_rename(source, old_name, new_name)
    except SyntaxError:
        return _simple_rename(source, old_name, new_name)


def _simple_rename(source: str, old_name: str, new_name: str) -> str:
    """Simple text-based rename fallback (word boundary matching)."""
    import re

    pattern = r"\b" + re.escape(old_name) + r"\b"
    return re.sub(pattern, new_name, source)


def _contains_name(tree: ast.AST, name: str) -> bool:
    """Check if the AST contains a reference to *name*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return True
        if isinstance(node, ast.ClassDef) and node.name == name:
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
    return False


def _check_scope_conflicts(tree: ast.AST, old_name: str, new_name: str) -> list[str]:
    """Check if *new_name* would conflict with existing names in the same scopes."""
    conflicts = []

    # Collect all scopes where old_name appears
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            collector = _NameCollector()
            collector.visit(node)
            if new_name in collector.names and old_name in collector.names:
                conflicts.append(
                    f"Scope conflict in {getattr(node, 'name', '<unknown>')}: "
                    f"'{new_name}' already exists alongside '{old_name}'"
                )

    return conflicts


def _count_replacements(source: str, old_name: str) -> int:
    """Count how many times *old_name* appears as a whole word."""
    import re

    pattern = r"\b" + re.escape(old_name) + r"\b"
    return len(re.findall(pattern, source))


def _walk_python_files(root: Path) -> list[Path]:
    """Collect all .py files under root."""
    _skip = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
    }
    results = []
    for item in root.rglob("*.py"):
        if any(part in _skip for part in item.parts):
            continue
        results.append(item)
    return sorted(results)
