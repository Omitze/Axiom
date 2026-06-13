"""Code metrics — McCabe cyclomatic complexity and Sonar-style cognitive complexity.

Provides two complexity measures for each function:

- **Cyclomatic complexity (McCabe)**: counts decision points (if, for, while,
  and, or, except, …). The minimum is 1 (a function with no branches).
- **Cognitive complexity (Sonar)**: like cyclomatic but penalises nesting
  depth and structural breaks (continue, break), giving a more human-
  intuitive measure of how hard code is to read.

Usage::

    from axiom.code_analysis.metrics import compute_complexity

    result = compute_complexity(source_code, "my_func", line_start=10)
    print(f"McCabe={result.cyclomatic}, Cognitive={result.cognitive}")
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass
class ComplexityResult:
    """Complexity measurements for a single function."""

    name: str
    cyclomatic: int = 1  # McCabe complexity (minimum = 1)
    cognitive: int = 0  # Sonar cognitive complexity

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "cyclomatic": self.cyclomatic,
            "cognitive": self.cognitive,
        }


# ======================================================================
#  McCabe Cyclomatic Complexity
# ======================================================================


class McCabeVisitor(ast.NodeVisitor):
    """Count decision points for McCabe cyclomatic complexity.

    Decision points: if, for, while, and, or, except, assert,
    comprehensions, ternary (IfExp), with (optional).
    """

    def __init__(self):
        self.complexity = 1  # base complexity

    # --- Decision points ---

    def visit_If(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_IfExp(self, node):  # ternary operator
        self.complexity += 1
        self.generic_visit(node)

    def visit_ListComp(self, node):
        # Each comprehension clause adds 1
        self.complexity += len(node.generators)
        self.generic_visit(node)

    def visit_SetComp(self, node):
        self.complexity += len(node.generators)
        self.generic_visit(node)

    def visit_DictComp(self, node):
        self.complexity += len(node.generators)
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        self.complexity += len(node.generators)
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        # 'and' / 'or' — each additional operand adds 1
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncWith(self, node):
        # Not always counted, but we include it for consistency
        self.complexity += 1
        self.generic_visit(node)


# ======================================================================
#  Cognitive Complexity (Sonar-style)
# ======================================================================


class CognitiveComplexityVisitor(ast.NodeVisitor):
    """Compute cognitive complexity following the Sonar spec.

    Key rules:
    - Increments for: if, elif, else, for, while, except, ternary,
      and, or, continue, break (in loops), nested function/class.
    - Nesting bonus: each nested control structure adds +1.
    - No increments for: else after if/elif/for/while/except/try,
      simple function calls, imports.
    """

    def __init__(self):
        self.complexity = 0
        self._nesting = 0

    def _increment(self, amount: int = 1):
        self.complexity += amount

    def _visit_structural(self, node, add_nesting: bool = True):
        """Visit a structural node (if, for, while, etc.).

        Adds 1 (base) + nesting depth (nesting bonus).
        """
        self._increment(1 + self._nesting)
        if add_nesting:
            self._nesting += 1
        self.generic_visit(node)
        if add_nesting:
            self._nesting -= 1

    # --- Control flow ---

    def visit_If(self, node):
        self._increment(1 + self._nesting)  # if
        # Visit the test expression (may contain BoolOp)
        self.visit(node.test)
        self._nesting += 1
        # Visit body
        for child in node.body:
            self.visit(child)
        self._nesting -= 1

        # elif and else
        for i, child in enumerate(node.orelse):
            if isinstance(child, ast.If):
                # elif: +1 (no nesting bonus)
                self._increment(1)
                self._nesting += 1
                for c in child.body:
                    self.visit(c)
                self._nesting -= 1
                # Recursively handle elif chain
                for j, oc in enumerate(child.orelse):
                    if isinstance(oc, ast.If):
                        self._increment(1)  # another elif
                        self._nesting += 1
                        for c in oc.body:
                            self.visit(c)
                        self._nesting -= 1
                        # Continue the chain
                        child.orelse = oc.orelse
                        self._handle_orelse(oc.orelse)
                        break
                    else:
                        self._increment(1)  # else
                        self.visit(oc)
                break
            else:
                self._increment(1)  # else
                self.visit(child)

    def _handle_orelse(self, orelse):
        """Helper to handle else/elif chains."""
        for child in orelse:
            if isinstance(child, ast.If):
                self._increment(1)  # elif
                self._nesting += 1
                for c in child.body:
                    self.visit(c)
                self._nesting -= 1
                self._handle_orelse(child.orelse)
            else:
                self._increment(1)  # else
                self.visit(child)

    def visit_For(self, node):
        self._increment(1 + self._nesting)
        self.visit(node.iter)  # visit iterator expression
        self._nesting += 1
        for child in node.body:
            self.visit(child)
        self._nesting -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_While(self, node):
        self._increment(1 + self._nesting)
        self.visit(node.test)  # visit test expression
        self._nesting += 1
        for child in node.body:
            self.visit(child)
        self._nesting -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_ExceptHandler(self, node):
        self._increment(1 + self._nesting)
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_IfExp(self, node):  # ternary
        self._increment(1 + self._nesting)
        self.generic_visit(node)

    # --- Boolean operators ---

    def visit_BoolOp(self, node):
        # Each operator adds +1 (no nesting bonus)
        self._increment(len(node.values) - 1)
        self.generic_visit(node)

    # --- Nested structures ---

    def visit_FunctionDef(self, node):
        if self._nesting > 0:
            # Nested function definition adds nesting
            self._increment(self._nesting)
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        if self._nesting > 0:
            self._increment(self._nesting)
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    # --- Loop control ---

    def visit_Break(self, node):
        self._increment(1)

    def visit_Continue(self, node):
        self._increment(1)


# ======================================================================
#  Public API
# ======================================================================


def compute_complexity(
    source: str,
    func_name: str | None = None,
    line_start: int | None = None,
) -> ComplexityResult:
    """Compute both McCabe and cognitive complexity for a function.

    Parameters
    ----------
    source:
        Python source code text.
    func_name:
        Name of the function to analyze. If None, analyzes the entire module.
    line_start:
        1-based line number where the function starts (used to locate it).

    Returns
    -------
    ComplexityResult
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ComplexityResult(name=func_name or "<module>", cyclomatic=1, cognitive=0)

    # Find the target function
    if func_name:
        func_node = _find_function(tree, func_name, line_start)
        if func_node is None:
            return ComplexityResult(name=func_name, cyclomatic=1, cognitive=0)
    else:
        func_node = tree

    # McCabe
    mccabe = McCabeVisitor()
    mccabe.visit(func_node)

    # Cognitive — we visit the *body* of the function, not the FunctionDef
    # itself, because the function definition is not a complexity increment.
    # The nesting level starts at 0 inside the function body.
    cognitive = CognitiveComplexityVisitor()
    if isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for stmt in func_node.body:
            cognitive.visit(stmt)
        # Also visit decorator defaults etc. — but skip the function name
    else:
        cognitive.visit(func_node)

    return ComplexityResult(
        name=func_name or "<module>",
        cyclomatic=mccabe.complexity,
        cognitive=cognitive.complexity,
    )


def _find_function(
    tree: ast.AST, func_name: str, line_start: int | None = None
) -> ast.AST | None:
    """Locate a function node in the AST by name (and optionally line)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                if line_start is None or node.lineno == line_start:
                    return node
        elif isinstance(node, ast.ClassDef):
            # Search for methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == func_name:
                        if line_start is None or item.lineno == line_start:
                            return item
    # If line_start didn't match, try just by name
    if line_start is not None:
        return _find_function(tree, func_name, line_start=None)
    return None
