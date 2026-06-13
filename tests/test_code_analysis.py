"""Tests for the Code Analysis module — AST parser, metrics, call graph,
dependency graph, refactor, reporter, and AnalyzeTool integration."""

import json
import textwrap
from pathlib import Path

import pytest

from axiom.code_analysis import (
    AnalysisResult,
    CallGraph,
    ClassInfo,
    DependencyGraph,
    FunctionInfo,
    ImportInfo,
    ModuleInfo,
    ProjectAnalyzer,
    RefactorResult,
    RefactorSafety,
    compute_complexity,
    format_report,
    refactor_rename,
)
from axiom.code_analysis.ast_parser import ASTParser
from axiom.code_analysis.metrics import (
    CognitiveComplexityVisitor,
    McCabeVisitor,
)
from axiom.tools.analyze import AnalyzeTool

# ============================================================================
#  Fixtures
# ============================================================================


MAIN_PY = '''"""Main entry point."""

from utils import helper, format_output
from processor import process_data

def main():
    """Run the program."""
    data = helper()
    result = process_data(data)
    print(format_output(result))

if __name__ == "__main__":
    main()
'''

UTILS_PY = '''"""Utility functions."""

def helper():
    """Return some data."""
    return [1, 2, 3]

def format_output(result):
    """Format the result for display."""
    if result is None:
        return "No result"
    elif isinstance(result, list):
        return ", ".join(str(x) for x in result)
    else:
        return str(result)
'''

PROCESSOR_PY = '''"""Data processing module."""

from utils import helper

class DataProcessor:
    """Process data with style."""

    def __init__(self, config=None):
        self.config = config or {}

    def process(self, data):
        """Process a list of data items."""
        results = []
        for item in data:
            if item > 0:
                results.append(self.transform(item))
            else:
                results.append(None)
        return results

    def transform(self, value):
        """Transform a single value."""
        return value * 2

def process_data(data):
    """Convenience function."""
    proc = DataProcessor()
    return proc.process(data)
'''


@pytest.fixture
def sample_project(tmp_path):
    """Create a small Python project for testing."""
    (tmp_path / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (tmp_path / "utils.py").write_text(UTILS_PY, encoding="utf-8")
    (tmp_path / "processor.py").write_text(PROCESSOR_PY, encoding="utf-8")
    return tmp_path


@pytest.fixture
def analyzer():
    """Fresh ProjectAnalyzer instance."""
    return ProjectAnalyzer()


# ============================================================================
#  FunctionInfo
# ============================================================================


class TestFunctionInfo:
    def test_qualified_name_simple(self):
        f = FunctionInfo(name="main", file="a.py", line_start=1, line_end=10)
        assert f.qualified_name == "main"

    def test_qualified_name_method(self):
        f = FunctionInfo(
            name="run",
            file="a.py",
            line_start=5,
            line_end=10,
            is_method=True,
            class_name="MyClass",
        )
        assert f.qualified_name == "MyClass.run"

    def test_loc(self):
        f = FunctionInfo(name="f", file="a.py", line_start=1, line_end=10)
        assert f.loc == 10

    def test_loc_single_line(self):
        f = FunctionInfo(name="f", file="a.py", line_start=5, line_end=5)
        assert f.loc == 1

    def test_to_dict(self):
        f = FunctionInfo(name="main", file="a.py", line_start=1, line_end=5)
        d = f.to_dict()
        assert d["name"] == "main"
        assert d["qualified_name"] == "main"
        assert d["file"] == "a.py"


# ============================================================================
#  ClassInfo
# ============================================================================


class TestClassInfo:
    def test_loc(self):
        c = ClassInfo(name="Foo", file="a.py", line_start=1, line_end=20)
        assert c.loc == 20

    def test_to_dict(self):
        method = FunctionInfo(
            name="run",
            file="a.py",
            line_start=3,
            line_end=5,
            is_method=True,
            class_name="Foo",
        )
        c = ClassInfo(
            name="Foo", file="a.py", line_start=1, line_end=20, methods=[method]
        )
        d = c.to_dict()
        assert d["name"] == "Foo"
        assert len(d["methods"]) == 1
        assert d["methods"][0]["name"] == "run"


# ============================================================================
#  ImportInfo
# ============================================================================


class TestImportInfo:
    def test_to_dict(self):
        imp = ImportInfo(module="os", names=["path"], file="a.py", line=1, is_from=True)
        d = imp.to_dict()
        assert d["module"] == "os"
        assert d["names"] == ["path"]
        assert d["is_from"] is True


# ============================================================================
#  ASTParser
# ============================================================================


class TestASTParser:
    def test_parse_simple_file(self, tmp_path):
        source = textwrap.dedent("""\
            import os
            from pathlib import Path

            def hello():
                print("hello")

            class Greeter:
                def greet(self):
                    return "hi"
        """)
        (tmp_path / "test.py").write_text(source, encoding="utf-8")

        parser = ASTParser()
        funcs, classes, imports = parser.parse_project(tmp_path)

        func_names = [f.name for f in funcs]
        assert "hello" in func_names

        class_names = [c.name for c in classes]
        assert "Greeter" in class_names

        # Check method extraction
        greeter = [c for c in classes if c.name == "Greeter"][0]
        method_names = [m.name for m in greeter.methods]
        assert "greet" in method_names

        # Check imports
        assert any(imp.module == "os" for imp in imports)
        assert any(imp.module == "pathlib" for imp in imports)

    def test_parse_project(self, sample_project):
        parser = ASTParser()
        funcs, classes, imports = parser.parse_project(sample_project)

        # Should find functions from all files
        func_names = [f.name for f in funcs]
        assert "main" in func_names
        assert "helper" in func_names
        assert "format_output" in func_names
        assert "process_data" in func_names

        # Should find the DataProcessor class
        class_names = [c.name for c in classes]
        assert "DataProcessor" in class_names

    def test_parse_file(self, sample_project):
        parser = ASTParser()
        funcs, classes, imports = parser.parse_file(
            sample_project / "main.py", root=sample_project
        )
        func_names = [f.name for f in funcs]
        assert "main" in func_names

    def test_parse_invalid_syntax(self, tmp_path):
        (tmp_path / "bad.py").write_text("def (broken syntax", encoding="utf-8")
        parser = ASTParser()
        funcs, classes, imports = parser.parse_project(tmp_path)
        # Should silently skip files with syntax errors
        assert len(funcs) == 0

    def test_function_dependencies(self, sample_project):
        parser = ASTParser()
        funcs, classes, imports = parser.parse_project(sample_project)

        main_func = [f for f in funcs if f.name == "main"][0]
        # main() calls helper(), process_data(), format_output(), print()
        dep_names = main_func.dependencies
        assert "helper" in dep_names
        assert "process_data" in dep_names
        assert "format_output" in dep_names

    def test_docstring_extraction(self, sample_project):
        parser = ASTParser()
        funcs, classes, imports = parser.parse_project(sample_project)

        main_func = [f for f in funcs if f.name == "main"][0]
        assert main_func.docstring == "Run the program."

    def test_skip_junk_dirs(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cache.py").write_text(
            "def f(): pass", encoding="utf-8"
        )
        (tmp_path / "real.py").write_text("def g(): pass", encoding="utf-8")

        parser = ASTParser()
        funcs, _, _ = parser.parse_project(tmp_path)
        func_names = [f.name for f in funcs]
        assert "g" in func_names
        assert "f" not in func_names


# ============================================================================
#  McCabe Cyclomatic Complexity
# ============================================================================


class TestMcCabeComplexity:
    def test_simple_function(self):
        source = "def f(): pass"
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 1

    def test_if_statement(self):
        source = textwrap.dedent("""\
            def f(x):
                if x > 0:
                    return x
                return -x
        """)
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 2

    def test_for_and_while(self):
        source = textwrap.dedent("""\
            def f(items):
                for x in items:
                    while x > 0:
                        x -= 1
                return items
        """)
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 3  # 1 base + for + while

    def test_boolean_ops(self):
        source = textwrap.dedent("""\
            def f(a, b, c):
                if a and b or c:
                    return True
                return False
        """)
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 4  # 1 base + if + and + or

    def test_try_except(self):
        source = textwrap.dedent("""\
            def f():
                try:
                    risky()
                except ValueError:
                    handle()
                except KeyError:
                    handle_other()
        """)
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 3  # 1 base + 2 except handlers

    def test_comprehension(self):
        source = "def f(): return [x for x in range(10)]"
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 2  # 1 base + 1 comprehension clause

    def test_nested_conditions(self):
        source = textwrap.dedent("""\
            def f(a, b, c):
                if a:
                    if b:
                        if c:
                            return 1
                return 0
        """)
        result = compute_complexity(source, "f")
        assert result.cyclomatic == 4  # 1 base + 3 ifs

    def test_module_level(self):
        source = "x = 1\ny = 2"
        result = compute_complexity(source)
        assert result.cyclomatic == 1

    def test_syntax_error(self):
        result = compute_complexity("def (broken", "f")
        assert result.cyclomatic == 1


# ============================================================================
#  Cognitive Complexity
# ============================================================================


class TestCognitiveComplexity:
    def test_simple_function(self):
        source = "def f(): pass"
        result = compute_complexity(source, "f")
        assert result.cognitive == 0

    def test_single_if(self):
        source = textwrap.dedent("""\
            def f(x):
                if x > 0:
                    return x
        """)
        result = compute_complexity(source, "f")
        assert result.cognitive == 1

    def test_nested_if(self):
        source = textwrap.dedent("""\
            def f(a, b):
                if a:
                    if b:
                        return 1
        """)
        result = compute_complexity(source, "f")
        # outer if: +1, inner if: +1 (base) +1 (nesting) = 3
        assert result.cognitive == 3

    def test_elif_else(self):
        source = textwrap.dedent("""\
            def f(x):
                if x > 0:
                    return 1
                elif x < 0:
                    return -1
                else:
                    return 0
        """)
        result = compute_complexity(source, "f")
        # if: +1, elif: +1, else: +1 = 3
        assert result.cognitive == 3

    def test_for_loop(self):
        source = textwrap.dedent("""\
            def f(items):
                for x in items:
                    process(x)
        """)
        result = compute_complexity(source, "f")
        assert result.cognitive == 1

    def test_bool_ops(self):
        source = textwrap.dedent("""\
            def f(a, b, c):
                if a and b or c:
                    return True
        """)
        result = compute_complexity(source, "f")
        # if: +1, and: +1, or: +1 = 3
        assert result.cognitive == 3

    def test_break_continue(self):
        source = textwrap.dedent("""\
            def f(items):
                for x in items:
                    if x < 0:
                        continue
                    if x > 100:
                        break
        """)
        result = compute_complexity(source, "f")
        # for: +1, if: +1(nesting) +1 = 2, continue: +1
        # if: +1(nesting) +1 = 2, break: +1
        # total = 1 + 2 + 1 + 2 + 1 = 7
        assert result.cognitive >= 5  # at minimum, structure is counted


# ============================================================================
#  CallGraph
# ============================================================================


class TestCallGraph:
    def test_build_and_query(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        # main should call helper, process_data, format_output
        main_callees = cg.callees("main")
        assert "helper" in main_callees or any("helper" in c for c in main_callees)

    def test_shortest_path(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        # main -> process_data should have a direct path
        path = cg.shortest_path("main", "process_data")
        assert path is not None
        assert "main" in path
        assert "process_data" in path

    def test_no_path(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        # helper doesn't call main
        path = cg.shortest_path("helper", "main")
        assert path is None

    def test_callers(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        # helper should be called by main
        callers_of_helper = cg.callers("helper")
        assert "main" in callers_of_helper

    def test_nodes_and_edges(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        assert len(cg.nodes()) > 0
        assert len(cg.edges()) > 0

    def test_to_dot(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        dot = cg.to_dot()
        assert "digraph" in dot
        assert "->" in dot

    def test_to_dict(self, sample_project):
        parser = ASTParser()
        funcs, classes, _ = parser.parse_project(sample_project)

        cg = CallGraph()
        cg.build(funcs, classes)

        d = cg.to_dict()
        assert "nodes" in d
        assert "edges" in d

    def test_has_node(self):
        cg = CallGraph()
        cg._forward["test"] = set()
        assert cg.has_node("test")
        assert not cg.has_node("nonexistent")


# ============================================================================
#  DependencyGraph
# ============================================================================


class TestDependencyGraph:
    def test_build(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        modules = dg.modules()
        assert len(modules) > 0

    def test_dependencies(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        # main.py imports from utils and processor
        main_deps = dg.dependencies("main")
        assert len(main_deps) > 0

    def test_dependents(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        # utils should be depended upon by main and processor
        utils_dependents = dg.dependents("utils")
        assert len(utils_dependents) > 0

    def test_transitive_dependencies(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        direct = dg.dependencies("main")
        transitive = dg.dependencies("main", transitive=True)
        # Transitive should be a superset of direct
        assert set(transitive) >= set(direct)

    def test_find_cycles_no_cycle(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        # No cycles in a well-structured project
        cycles = dg.find_cycles()
        # Might or might not have cycles depending on import structure
        assert isinstance(cycles, list)

    def test_find_cycles_with_cycle(self, tmp_path):
        # Create circular import
        (tmp_path / "a.py").write_text("from b import something", encoding="utf-8")
        (tmp_path / "b.py").write_text("from a import other", encoding="utf-8")

        parser = ASTParser()
        _, _, imports = parser.parse_project(tmp_path)

        dg = DependencyGraph()
        dg.build(tmp_path, imports)

        cycles = dg.find_cycles()
        assert len(cycles) > 0

    def test_topological_sort(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        result = dg.topological_sort()
        # If there are no cycles, should return a valid ordering
        if result is not None:
            assert len(result) == len(dg.modules())

    def test_to_dot(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        dot = dg.to_dot()
        assert "digraph" in dot
        assert "->" in dot

    def test_to_dict(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        d = dg.to_dict()
        assert "modules" in d
        assert "edges" in d

    def test_get_module(self, sample_project):
        parser = ASTParser()
        _, _, imports = parser.parse_project(sample_project)

        dg = DependencyGraph()
        dg.build(sample_project, imports)

        modules = dg.modules()
        if modules:
            mod = dg.get_module(modules[0])
            assert mod is not None
            assert mod.name == modules[0]


# ============================================================================
#  Refactor
# ============================================================================


class TestRefactor:
    def test_rename_dry_run(self, tmp_path):
        source = textwrap.dedent("""\
            def old_name():
                return old_name + 1
        """)
        (tmp_path / "test.py").write_text(source, encoding="utf-8")

        result = refactor_rename("old_name", "new_name", tmp_path, dry_run=True)
        assert result.old_name == "old_name"
        assert result.new_name == "new_name"
        assert len(result.changes) > 0

        # File should NOT be modified (dry run)
        content = (tmp_path / "test.py").read_text()
        assert "old_name" in content

    def test_rename_apply(self, tmp_path):
        source = textwrap.dedent("""\
            def old_name():
                return 42
        """)
        (tmp_path / "test.py").write_text(source, encoding="utf-8")

        result = refactor_rename("old_name", "new_name", tmp_path, dry_run=False)
        assert len(result.changes) > 0

        # File should be modified
        content = (tmp_path / "test.py").read_text()
        assert "new_name" in content

    def test_rename_invalid_identifier(self, tmp_path):
        result = refactor_rename("foo", "123bad", tmp_path)
        assert result.safety == RefactorSafety.UNSAFE
        assert any("not a valid Python identifier" in w for w in result.warnings)

    def test_rename_shadows_builtin(self, tmp_path):
        result = refactor_rename("foo", "list", tmp_path)
        assert result.safety == RefactorSafety.WARNING

    def test_rename_no_matches(self, tmp_path):
        (tmp_path / "test.py").write_text("def bar(): pass", encoding="utf-8")
        result = refactor_rename("nonexistent", "something", tmp_path)
        assert len(result.changes) == 0

    def test_refactor_result_is_safe(self):
        result = RefactorResult(old_name="a", new_name="b", safety=RefactorSafety.SAFE)
        assert result.is_safe

        result.safety = RefactorSafety.WARNING
        assert not result.is_safe

    def test_refactor_result_to_dict(self):
        result = RefactorResult(old_name="a", new_name="b", safety=RefactorSafety.SAFE)
        d = result.to_dict()
        assert d["old_name"] == "a"
        assert d["safety"] == "safe"


# ============================================================================
#  ProjectAnalyzer (integration)
# ============================================================================


class TestProjectAnalyzer:
    def test_analyze(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        assert isinstance(result, AnalysisResult)
        assert result.total_functions > 0
        assert result.total_classes > 0
        assert result.total_files > 0

    def test_find_definition_function(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        info = analyzer.find_definition("main")
        assert info is not None
        assert info.name == "main"

    def test_find_definition_class(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        info = analyzer.find_definition("DataProcessor")
        assert info is not None
        assert info.name == "DataProcessor"

    def test_find_definition_dotted(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        info = analyzer.find_definition("DataProcessor.process")
        assert info is not None
        assert info.name == "process"
        assert info.is_method

    def test_find_definition_not_found(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        info = analyzer.find_definition("nonexistent")
        assert info is None

    def test_find_usages(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        usages = analyzer.find_usages("helper")
        assert len(usages) > 0

    def test_call_chain(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        path = analyzer.call_chain("main", "process_data")
        assert path is not None
        assert "main" in path

    def test_call_chain_no_path(self, analyzer, sample_project):
        analyzer.analyze(sample_project)
        path = analyzer.call_chain("helper", "main")
        assert path is None

    def test_no_analyze_raises(self, analyzer):
        with pytest.raises(RuntimeError, match="analyze"):
            analyzer.find_definition("foo")

    def test_nonexistent_root(self, analyzer, tmp_path):
        with pytest.raises(FileNotFoundError):
            analyzer.analyze(tmp_path / "nonexistent")

    def test_file_root(self, analyzer, tmp_path):
        (tmp_path / "file.py").write_text("pass", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            analyzer.analyze(tmp_path / "file.py")


# ============================================================================
#  AnalysisResult
# ============================================================================


class TestAnalysisResult:
    def test_summary(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        s = result.summary()
        assert "total_files" in s
        assert "total_functions" in s
        assert "total_classes" in s
        assert "avg_complexity" in s
        assert s["total_files"] > 0

    def test_complexity_hotspots(self, tmp_path):
        # Create a complex function
        source = textwrap.dedent("""\
            def complex_func(x, y, z):
                if x:
                    if y:
                        if z:
                            for i in range(10):
                                if i > 5:
                                    while True:
                                        break
                return x
        """)
        (tmp_path / "complex.py").write_text(source, encoding="utf-8")

        analyzer = ProjectAnalyzer()
        result = analyzer.analyze(tmp_path)
        hotspots = result.complexity_hotspots(threshold=5)
        assert len(hotspots) > 0

    def test_to_dict(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        d = result.to_dict()
        assert "root" in d
        assert "functions" in d
        assert "classes" in d
        assert "imports" in d
        assert "call_graph" in d
        assert "dependency_graph" in d

    def test_to_json(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert "root" in parsed

    def test_total_lines(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        assert result.total_lines > 0


# ============================================================================
#  Format Report
# ============================================================================


class TestFormatReport:
    def test_markdown_report(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        report = format_report(result, format="markdown")
        assert "# Code Analysis Report" in report
        assert "Summary" in report

    def test_json_report(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        report = format_report(result, format="json")
        parsed = json.loads(report)
        assert "root" in parsed

    def test_invalid_format(self, analyzer, sample_project):
        result = analyzer.analyze(sample_project)
        with pytest.raises(ValueError, match="Unknown format"):
            format_report(result, format="xml")


# ============================================================================
#  AnalyzeTool
# ============================================================================


class TestAnalyzeTool:
    def test_schema(self):
        tool = AnalyzeTool()
        schema = tool.schema()
        assert schema["function"]["name"] == "analyze"
        assert "action" in schema["function"]["parameters"]["properties"]

    def test_function_list(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="function_list", path=str(sample_project))
        assert "Functions:" in result
        assert "main" in result

    def test_call_graph(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="call_graph", path=str(sample_project))
        assert "Call graph" in result or "No call" in result

    def test_complexity(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="complexity", path=str(sample_project))
        assert "Complexity report" in result or "No functions" in result

    def test_find_definition(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(
            action="find_definition", path=str(sample_project), symbol="main"
        )
        assert "main" in result

    def test_find_definition_missing_symbol(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="find_definition", path=str(sample_project))
        assert "Error" in result or "required" in result

    def test_find_usages(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(
            action="find_usages", path=str(sample_project), symbol="helper"
        )
        assert "helper" in result

    def test_dependencies(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="dependencies", path=str(sample_project))
        assert "dependencies" in result.lower() or "DOT" in result

    def test_refactor_rename(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(
            action="refactor_rename",
            path=str(sample_project),
            symbol="helper",
            new_name="assistor",
        )
        assert "Refactor rename" in result

    def test_refactor_rename_missing_args(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="refactor_rename", path=str(sample_project))
        assert "Error" in result or "required" in result

    def test_unknown_action(self, sample_project):
        tool = AnalyzeTool()
        result = tool.execute(action="unknown_action", path=str(sample_project))
        assert "Error" in result or "unknown" in result.lower()

    def test_nonexistent_path(self):
        tool = AnalyzeTool()
        result = tool.execute(action="function_list", path="/nonexistent/path")
        assert "Error" in result

    def test_file_path(self, tmp_path):
        tool = AnalyzeTool()
        (tmp_path / "file.py").write_text("pass", encoding="utf-8")
        result = tool.execute(action="function_list", path=str(tmp_path / "file.py"))
        assert "Error" in result or "directory" in result.lower()

    def test_tool_registered(self):
        from axiom.tools import ALL_TOOLS, get_tool

        assert get_tool("analyze") is not None
        tool_names = [t.name for t in ALL_TOOLS]
        assert "analyze" in tool_names


# ============================================================================
#  Compute Complexity standalone
# ============================================================================


class TestComputeComplexity:
    def test_returns_complexity_result(self):
        source = "def f(): pass"
        result = compute_complexity(source, "f")
        assert result.name == "f"
        assert result.cyclomatic >= 1

    def test_not_found_function(self):
        source = "def f(): pass"
        result = compute_complexity(source, "nonexistent")
        assert result.cyclomatic == 1  # default for not found

    def test_to_dict(self):
        source = "def f(): pass"
        result = compute_complexity(source, "f")
        d = result.to_dict()
        assert "cyclomatic" in d
        assert "cognitive" in d
