"""Tool registry."""

from .agent import AgentTool
from .analyze import AnalyzeTool
from .bash import BashTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .write import WriteFileTool

ALL_TOOLS = [
    BashTool(),
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    GlobTool(),
    GrepTool(),
    AgentTool(),
    AnalyzeTool(),
]


def get_tool(name: str):
    """Look up a tool by name."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None


def get_tools_by_name(names: list[str]) -> list:
    """Look up multiple tools by name."""
    return [t for t in ALL_TOOLS if t.name in names]
