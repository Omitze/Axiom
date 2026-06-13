"""Axiom Skills Module — dynamically loadable tool extensions.

This module implements a **pluggable skill architecture** that lets users,
pip packages, and the Dream & Distill subsystem contribute new Tools
that are discovered at runtime.

Typical usage::

    from axiom.skills import SkillLoader, SkillRegistry

    loader = SkillLoader()
    tools = loader.discover()                # load from all sources
    loader.load_from_dir(Path("./my_skills"))  # load from a custom dir

    # Pass to Agent alongside built-in tools:
    agent = Agent(llm=llm, tools=ALL_TOOLS + tools)
"""

from .generator import generate_skill, validate_skill
from .loader import SkillLoader, load_skill
from .manager import SkillManager
from .registry import SkillRegistry
from .spec import SkillError, SkillNotFoundError, SkillValidationError, ValidationResult

__all__ = [
    "SkillLoader",
    "SkillRegistry",
    "SkillManager",
    "load_skill",
    "validate_skill",
    "generate_skill",
    "ValidationResult",
    "SkillError",
    "SkillNotFoundError",
    "SkillValidationError",
]
