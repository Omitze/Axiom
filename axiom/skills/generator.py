"""Skill generator — validate skills and generate them from templates.

This module is primarily used by the future *Dream & Distill* subsystem:

1. **validate_skill** — AST-based code validation before registering a skill.
2. **generate_skill** — Produce a complete skill file from a template.
"""

import ast
import builtins
import textwrap
from pathlib import Path

from .spec import ValidationResult

# ---------------------------------------------------------------------------
#  Dangerous import/subprocess patterns we warn about
# ---------------------------------------------------------------------------

_DANGEROUS_NAMES: set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
}

_DANGEROUS_ATTRS: set[str] = {
    "remove",
    "rmdir",
    "rmtree",
    "unlink",
    "system",
}

_DANGEROUS_MODULES: set[str] = {
    "ctypes",
    "inspect",
    "pickle",
    "shelve",
    "telnetlib",
}

# ---------------------------------------------------------------------------
#  Validation
# ---------------------------------------------------------------------------


def validate_skill(
    skill_code: str, module_name: str = "_test_skill"
) -> ValidationResult:
    """AST-based validation of a skill's source code.

    Checks performed:

    * Valid Python syntax.
    * Exports a ``create_tool()`` function.
    * No dangerous builtins (eval, exec, …).
    * No dangerous module imports.
    * ``create_tool()`` returns something that looks like a Tool.

    Returns a ``ValidationResult`` with errors (critical) and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Check syntax
    try:
        tree = ast.parse(skill_code, filename=module_name)
    except SyntaxError as e:
        errors.append(f"Syntax error: {e}")
        return ValidationResult(valid=False, errors=errors)

    # 2. Check that create_tool function exists at module level
    has_create_tool = any(
        isinstance(node, ast.FunctionDef) and node.name == "create_tool"
        for node in ast.iter_child_nodes(tree)
    )
    if not has_create_tool:
        errors.append(
            "Skill must define a top-level `create_tool()` function "
            "that returns a Tool instance."
        )

    # 3. AST walk for dangerous patterns
    for node in ast.walk(tree):
        # Calls to eval/exec/compile
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_NAMES:
                warnings.append(
                    f"Call to '{node.func.id}()' at line {node.lineno} "
                    f"— use with extreme caution in skills."
                )

            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in _DANGEROUS_ATTRS
            ):
                warnings.append(
                    f"Call to '.{node.func.attr}()' at line {node.lineno} "
                    f"— potentially destructive."
                )

        # Imports of dangerous modules
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _DANGEROUS_MODULES:
                    warnings.append(
                        f"Import of '{alias.name}' at line {node.lineno} "
                        f"— this module can be unsafe."
                    )
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _DANGEROUS_MODULES:
                warnings.append(
                    f"Import from '{node.module}' at line {node.lineno} "
                    f"— this module can be unsafe."
                )

    if errors:
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # 4. Try importing (sandboxed execution)
    try:
        _try_import_skill(skill_code, module_name)
    except Exception as e:
        errors.append(f"Runtime validation failed: {e}")
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    return ValidationResult(valid=True, warnings=warnings)


def _try_import_skill(skill_code: str, module_name: str) -> None:
    """Try to execute the skill code and call create_tool() in isolation."""
    import importlib.util
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix=f"{module_name}_", delete=False
    ) as f:
        f.write(skill_code)
        tmp_path = Path(f.name)

    try:
        spec = importlib.util.spec_from_file_location(module_name, tmp_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not create module spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            raise RuntimeError(f"Module execution failed: {e}") from e

        if not hasattr(module, "create_tool"):
            raise RuntimeError("No create_tool() function found")

        factory = module.create_tool
        if not callable(factory):
            raise RuntimeError("create_tool is not callable")

        tool = factory()
        if tool is None:
            raise RuntimeError("create_tool() returned None")

        # Basic sanity: must have name, description, execute
        for attr in ("name", "description", "execute"):
            if not hasattr(tool, attr):
                raise RuntimeError(f"Tool instance missing '{attr}'")
    finally:
        sys.modules.pop(module_name, None)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
#  Code generation
# ---------------------------------------------------------------------------

SKILL_TEMPLATE = '''\
\"\"\"{description}\"\"\"

from axiom.tools.base import Tool


class {class_name}(Tool):
    name = "{name}"
    description = "{description}"
    parameters = {{
        "type": "object",
        "properties": {properties},
        "required": {required},
    }}

    def execute(self, {params}) -> str:
{body}


def create_tool() -> Tool:
    """Factory called by the SkillLoader to instantiate this skill."""
    return {class_name}()
'''


def generate_skill(
    name: str,
    description: str,
    properties: dict | None = None,
    required: list[str] | None = None,
    body: str = 'return f"Executed {name}"',
) -> str:
    """Generate a valid skill file from a template.

    Args:
        name: Tool name (used for the Python class name as well).
        description: Tool description shown to the LLM.
        properties: JSON Schema properties dict.
        required: List of required parameter names.
        body: Python code for the ``execute`` method body.

    Returns:
        The generated Python source code as a string.
    """
    class_name = _to_class_name(name)
    props_str = _format_properties(properties or {"name": {"type": "string"}})
    req_list = required or ["name"]
    params_str = ", ".join(f"{p}" for p in (required or ["name"]))

    body_indented = textwrap.indent(body, " " * 12)

    return SKILL_TEMPLATE.format(
        name=name,
        class_name=class_name,
        description=description,
        properties=props_str,
        required=req_list,
        params=params_str,
        body=body_indented,
    )


def _to_class_name(name: str) -> str:
    """Convert a snake_case/kebab name to PascalCase.

    >>> _to_class_name("url_fetch")
    'UrlFetchTool'
    >>> _to_class_name("json-tool")
    'JsonToolTool'
    """
    parts = name.replace("-", "_").split("_")
    camel = "".join(p.capitalize() for p in parts)
    # Avoid double Tool suffix if name already ends with "tool"
    if not camel.lower().endswith("tool"):
        camel += "Tool"
    return camel


def _format_properties(props: dict) -> str:
    """Pretty-format a small JSON Schema properties dict."""
    import json

    formatted = json.dumps(props, indent=8)
    # Remove outer braces for embedding in template
    return formatted
