"""Axiom Code Analysis Module — AST-based static code analysis engine.

Provides structural code understanding that goes beyond text-level grep/glob:
- **Function / class extraction** : name, location, docstring, dependencies
- **Call graph construction**    : who-calls-whom with shortest-path queries
- **Code metrics**               : McCabe cyclomatic complexity, cognitive complexity
- **Dependency graph**           : module-level import relationships
- **AST-level refactoring**      : scope-aware rename, extract function

Typical usage::

    from axiom.code_analysis import ProjectAnalyzer

    analyzer = ProjectAnalyzer()
    result = analyzer.analyze(Path("my_project"))
    print(result.summary())

    # Find a symbol definition
    info = analyzer.find_definition("main")
    print(info)

    # Trace a call chain
    path = analyzer.call_chain("main", "process_data")
    print(path)

    # Get complexity hotspots
    hotspots = result.complexity_hotspots(threshold=10)
    for func in hotspots:
        print(f"{func.name}: complexity={func.complexity}")
"""

from .ast_parser import ASTParser, ClassInfo, FunctionInfo, ImportInfo
from .call_graph import CallGraph
from .dependency_graph import DependencyGraph, ModuleInfo
from .metrics import CognitiveComplexityVisitor, compute_complexity
from .refactor import RefactorResult, RefactorSafety, refactor_rename
from .reporter import AnalysisResult, format_report

__all__ = [
    "ProjectAnalyzer",
    "AnalysisResult",
    "FunctionInfo",
    "ClassInfo",
    "ImportInfo",
    "CallGraph",
    "DependencyGraph",
    "ModuleInfo",
    "ASTParser",
    "compute_complexity",
    "CognitiveComplexityVisitor",
    "refactor_rename",
    "RefactorResult",
    "RefactorSafety",
    "format_report",
]


class ProjectAnalyzer:
    """High-level API: scan a project and answer structural queries.

    Usage::

        analyzer = ProjectAnalyzer()
        result = analyzer.analyze(Path("src"))
        info = analyzer.find_definition("MyClass.method")
        usages = analyzer.find_usages("MyClass.method")
        chain = analyzer.call_chain("main", "process_data")
    """

    def __init__(self):
        self._parser = ASTParser()
        self._result: AnalysisResult | None = None

    def analyze(self, root) -> "AnalysisResult":
        """Scan a project directory and build the complete code structure graph.

        Parameters
        ----------
        root:
            Path to the project root directory.

        Returns
        -------
        AnalysisResult
            Structured analysis result with all extracted information.
        """
        from pathlib import Path

        root = Path(root).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Project root not found: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"Expected a directory: {root}")

        # Parse all Python files
        functions, classes, imports = self._parser.parse_project(root)

        # Build call graph
        call_graph = CallGraph()
        call_graph.build(functions, classes)

        # Build dependency graph
        dep_graph = DependencyGraph()
        dep_graph.build(root, imports)

        # Compute complexity for each function
        for func in functions:
            file_path = (
                root / func.file if not _is_absolute(func.file) else Path(func.file)
            )
            try:
                source = file_path.read_text(errors="ignore")
                cplx = compute_complexity(source, func.name, func.line_start)
                func.complexity = cplx.cyclomatic
                func.cognitive_complexity = cplx.cognitive
            except OSError:
                pass

        self._result = AnalysisResult(
            root=str(root),
            functions=functions,
            classes=classes,
            imports=imports,
            call_graph=call_graph,
            dependency_graph=dep_graph,
        )
        return self._result

    def find_definition(self, symbol: str) -> "FunctionInfo | ClassInfo | None":
        """Find the definition location of a symbol.

        Supports dotted names like ``MyClass.method`` — will search for
        the class first, then look for the method inside it.

        Parameters
        ----------
        symbol:
            Function or class name (optionally dotted).

        Returns
        -------
        FunctionInfo | ClassInfo | None
        """
        if self._result is None:
            raise RuntimeError("Call analyze() first")

        # Handle dotted names: "ClassName.method_name"
        if "." in symbol:
            parts = symbol.split(".", 1)
            class_name, member_name = parts
            for cls in self._result.classes:
                if cls.name == class_name:
                    for method in cls.methods:
                        if method.name == member_name:
                            return method
            return None

        # Search functions
        for func in self._result.functions:
            if func.name == symbol:
                return func

        # Search classes
        for cls in self._result.classes:
            if cls.name == symbol:
                return cls

        return None

    def find_usages(self, symbol: str) -> list:
        """Find all locations where *symbol* is referenced.

        Parameters
        ----------
        symbol:
            Function, class, or variable name to search for.

        Returns
        -------
        list[FunctionInfo | ClassInfo]
        """
        if self._result is None:
            raise RuntimeError("Call analyze() first")

        usages = []

        # Check if symbol appears in any function's dependencies
        for func in self._result.functions:
            if symbol in func.dependencies:
                usages.append(func)

        # Check class methods
        for cls in self._result.classes:
            for method in cls.methods:
                if symbol in method.dependencies:
                    usages.append(method)

        # Check if the symbol itself is a class and is referenced in imports
        for imp in self._result.imports:
            if symbol in imp.names:
                usages.append(imp)

        return usages

    def call_chain(self, from_func: str, to_func: str) -> list[str] | None:
        """Find the shortest call path between two functions.

        Parameters
        ----------
        from_func:
            Starting function name.
        to_func:
            Target function name.

        Returns
        -------
        list[str] | None
            Ordered list of function names from *from_func* to *to_func*,
            or None if no path exists.
        """
        if self._result is None:
            raise RuntimeError("Call analyze() first")
        return self._result.call_graph.shortest_path(from_func, to_func)

    def refactor_rename(
        self, old_name: str, new_name: str, root=None
    ) -> "RefactorResult":
        """Safely rename a symbol across the project.

        Performs scope-aware checks before applying changes.

        Parameters
        ----------
        old_name:
            Current symbol name.
        new_name:
            Desired new name.
        root:
            Project root (uses the analyzed root if not provided).

        Returns
        -------
        RefactorResult
        """
        from pathlib import Path

        if self._result is None:
            raise RuntimeError("Call analyze() first")

        project_root = Path(root) if root else Path(self._result.root)
        return refactor_rename(old_name, new_name, project_root)


def _is_absolute(path_str: str) -> bool:
    from pathlib import Path

    try:
        return Path(path_str).is_absolute()
    except Exception:
        return False
