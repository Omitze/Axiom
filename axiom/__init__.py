"""Axiom - LLM-powered autonomous coding agent."""

__version__ = "0.5.0"

from axiom.agent import Agent
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
from axiom.config import Config
from axiom.dream_distill import (
    AutoTrigger,
    DistillResult,
    DreamDistillEngine,
    DreamReport,
    MemoryConsolidator,
    PatternMiner,
    SkillPackager,
    SmartForgetter,
    WorkflowPattern,
    prefixspan_mine,
)
from axiom.goal import (
    Goal,
    GoalJudgeEngine,
    GoalManager,
    Judge,
    JudgeVerdict,
    VerdictItem,
    VerifierChain,
)
from axiom.llm import LLM
from axiom.memory import (
    MemoryItem,
    MemoryManager,
    MemorySearch,
    MemoryStorage,
    MemoryType,
)
from axiom.skills import (
    SkillLoader,
    SkillManager,
    SkillRegistry,
    generate_skill,
    load_skill,
    validate_skill,
)
from axiom.tools import ALL_TOOLS

__all__ = [
    "Agent",
    "LLM",
    "Config",
    "ALL_TOOLS",
    "SkillLoader",
    "SkillRegistry",
    "SkillManager",
    "load_skill",
    "validate_skill",
    "generate_skill",
    "MemoryManager",
    "MemoryItem",
    "MemoryType",
    "MemoryStorage",
    "MemorySearch",
    "ProjectAnalyzer",
    "AnalysisResult",
    "FunctionInfo",
    "ClassInfo",
    "ImportInfo",
    "CallGraph",
    "DependencyGraph",
    "ModuleInfo",
    "compute_complexity",
    "refactor_rename",
    "RefactorResult",
    "RefactorSafety",
    "format_report",
    "DreamDistillEngine",
    "MemoryConsolidator",
    "SmartForgetter",
    "PatternMiner",
    "SkillPackager",
    "AutoTrigger",
    "prefixspan_mine",
    "DreamReport",
    "WorkflowPattern",
    "DistillResult",
    "Goal",
    "GoalJudgeEngine",
    "GoalManager",
    "Judge",
    "JudgeVerdict",
    "VerdictItem",
    "VerifierChain",
    "__version__",
]
