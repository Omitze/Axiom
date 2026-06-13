"""Python AST parser — extract functions, classes, and imports from source code.

Walks the Python AST for each file in a project and extracts structured
information about every function, class, and import statement.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

# Directories to skip during project scanning
_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
}


@dataclass
class FunctionInfo:
    """Metadata about a single function or method."""

    name: str
    file: str  # relative path from project root
    line_start: int
    line_end: int
    complexity: int = 0  # McCabe cyclomatic complexity
    cognitive_complexity: int = 0  # Cognitive complexity (Sonar-style)
    dependencies: list[str] = field(default_factory=list)  # called function names
    called_by: list[str] = field(
        default_factory=list
    )  # callers (populated by CallGraph)
    docstring: str | None = None
    is_method: bool = False
    class_name: str | None = None  # set when is_method=True

    @property
    def qualified_name(self) -> str:
        """Return dotted qualified name (e.g. MyClass.method)."""
        if self.class_name:
            return f"{self.class_name}.{self.name}"
        return self.name

    @property
    def loc(self) -> int:
        """Lines of code."""
        return max(1, self.line_end - self.line_start + 1)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "complexity": self.complexity,
            "cognitive_complexity": self.cognitive_complexity,
            "dependencies": list(self.dependencies),
            "called_by": list(self.called_by),
            "docstring": self.docstring,
            "is_method": self.is_method,
            "class_name": self.class_name,
        }


@dataclass
class ClassInfo:
    """Metadata about a single class."""

    name: str
    file: str
    line_start: int
    line_end: int
    bases: list[str] = field(default_factory=list)
    methods: list[FunctionInfo] = field(default_factory=list)
    docstring: str | None = None

    @property
    def loc(self) -> int:
        return max(1, self.line_end - self.line_start + 1)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "bases": list(self.bases),
            "methods": [m.to_dict() for m in self.methods],
            "docstring": self.docstring,
        }


@dataclass
class ImportInfo:
    """Information about a single import statement."""

    module: str  # the module being imported from
    names: list[str] = field(default_factory=list)  # names imported
    file: str = ""
    line: int = 0
    is_from: bool = False  # from X import Y vs import X

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "names": list(self.names),
            "file": self.file,
            "line": self.line,
            "is_from": self.is_from,
        }


class ASTParser:
    """Parse Python source files and extract structural information.

    Usage::

        parser = ASTParser()
        functions, classes, imports = parser.parse_project(Path("src"))
    """

    def parse_project(
        self, root: Path
    ) -> tuple[list[FunctionInfo], list[ClassInfo], list[ImportInfo]]:
        """Scan all Python files under *root* and extract structural info.

        Returns
        -------
        (functions, classes, imports)
            Three lists of extracted information.
        """
        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []

        for py_file in self._walk_python_files(root):
            rel = str(py_file.relative_to(root)).replace("\\", "/")
            try:
                source = py_file.read_text(errors="ignore")
                tree = ast.parse(source, filename=rel)
            except SyntaxError:
                continue

            file_funcs, file_classes, file_imports = self.parse_tree(tree, rel)
            functions.extend(file_funcs)
            classes.extend(file_classes)
            imports.extend(file_imports)

        return functions, classes, imports

    def parse_tree(
        self, tree: ast.AST, file: str
    ) -> tuple[list[FunctionInfo], list[ClassInfo], list[ImportInfo]]:
        """Extract info from a single AST tree.

        Parameters
        ----------
        tree:
            Parsed AST.
        file:
            Relative file path for annotation.

        Returns
        -------
        (functions, classes, imports)
        """
        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []

        for node in ast.walk(tree):
            # --- Imports ---
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(
                        ImportInfo(
                            module=alias.name,
                            names=[alias.asname or alias.name],
                            file=file,
                            line=node.lineno,
                            is_from=False,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.asname or alias.name for alias in node.names]
                imports.append(
                    ImportInfo(
                        module=module,
                        names=names,
                        file=file,
                        line=node.lineno,
                        is_from=True,
                    )
                )

            # --- Classes ---
            elif isinstance(node, ast.ClassDef):
                cls_info = self._extract_class(node, file)
                classes.append(cls_info)
                # Class methods are also tracked as functions
                functions.extend(cls_info.methods)

            # --- Top-level functions ---
            elif isinstance(node, ast.FunctionDef) or isinstance(
                node, ast.AsyncFunctionDef
            ):
                # Skip methods — they're already extracted via ClassDef
                if not self._is_method(tree, node):
                    func_info = self._extract_function(node, file)
                    functions.append(func_info)

        return functions, classes, imports

    def parse_file(
        self, path: Path, root: Path | None = None
    ) -> tuple[list[FunctionInfo], list[ClassInfo], list[ImportInfo]]:
        """Parse a single Python file.

        Parameters
        ----------
        path:
            Path to the Python file.
        root:
            Optional project root for computing relative paths.

        Returns
        -------
        (functions, classes, imports)
        """
        try:
            source = path.read_text(errors="ignore")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return [], [], []

        if root:
            rel = str(path.relative_to(root)).replace("\\", "/")
        else:
            rel = str(path).replace("\\", "/")

        return self.parse_tree(tree, rel)

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _extract_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file: str,
        class_name: str | None = None,
    ) -> FunctionInfo:
        """Extract FunctionInfo from an AST function node."""
        docstring = ast.get_docstring(node)
        deps = self._extract_call_names(node)
        end_line = self._get_end_line(node)

        return FunctionInfo(
            name=node.name,
            file=file,
            line_start=node.lineno,
            line_end=end_line,
            dependencies=deps,
            docstring=docstring,
            is_method=class_name is not None,
            class_name=class_name,
        )

    def _extract_class(self, node: ast.ClassDef, file: str) -> ClassInfo:
        """Extract ClassInfo from an AST class node."""
        docstring = ast.get_docstring(node)
        bases = [
            ast.unparse(b) if hasattr(ast, "unparse") else self._format_base(b)
            for b in node.bases
        ]
        end_line = self._get_end_line(node)
        methods = []

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_info = self._extract_function(item, file, class_name=node.name)
                methods.append(method_info)

        return ClassInfo(
            name=node.name,
            file=file,
            line_start=node.lineno,
            line_end=end_line,
            bases=bases,
            methods=methods,
            docstring=docstring,
        )

    def _extract_call_names(self, node: ast.AST) -> list[str]:
        """Extract names of functions called within *node*."""
        calls: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._call_name(child)
                if name:
                    calls.append(name)
        # Deduplicate while preserving order
        seen = set()
        result = []
        for c in calls:
            if c not in seen:
                seen.add(c)
                result.append(c)
        return result

    @staticmethod
    def _call_name(call: ast.Call) -> str | None:
        """Extract a readable name from a Call node."""
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        elif isinstance(func, ast.Attribute):
            # For method calls like obj.method(), return "method"
            return func.attr
        return None

    @staticmethod
    def _is_method(tree: ast.AST, func_node: ast.AST) -> bool:
        """Check if a function node is a method inside a class."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if item is func_node:
                        return True
        return False

    @staticmethod
    def _get_end_line(node: ast.AST) -> int:
        """Get the last line number of an AST node."""
        end_line = node.lineno if hasattr(node, "lineno") else 0
        if hasattr(node, "end_lineno") and node.end_lineno:
            return node.end_lineno
        # Fallback: walk all child nodes
        for child in ast.walk(node):
            if hasattr(child, "lineno"):
                end_line = max(end_line, child.lineno)
            if hasattr(child, "end_lineno") and child.end_lineno:
                end_line = max(end_line, child.end_lineno)
        return end_line

    @staticmethod
    def _format_base(base: ast.expr) -> str:
        """Format a base class expression (fallback for Python < 3.9)."""
        if isinstance(base, ast.Name):
            return base.id
        if isinstance(base, ast.Attribute):
            return f"{ASTParser._format_base(base.value)}.{base.attr}"
        return "..."

    @staticmethod
    def _walk_python_files(root: Path) -> list[Path]:
        """Collect all .py files under root, skipping noise dirs."""
        results = []
        for item in root.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in item.parts):
                continue
            results.append(item)
        return sorted(results)
