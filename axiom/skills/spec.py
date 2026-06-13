"""Data models for the skills system."""

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of validating a skill's source code."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SkillError(Exception):
    """Base exception for skill-related errors."""


class SkillNotFoundError(SkillError):
    """Raised when a skill is not found in the registry or on disk."""


class SkillValidationError(SkillError):
    """Raised when a skill's code fails validation."""
