"""Core agent loop.

This is the heart of Axiom.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import concurrent.futures
from pathlib import Path

from .context import ContextManager
from .llm import LLM
from .prompt import system_prompt
from .tools import ALL_TOOLS, get_tool
from .tools.agent import AgentTool
from .tools.base import Tool


# attachable subsystems (lazy-imported so they don't block startup)
def _load_memory():
    from .memory import MemoryManager

    return MemoryManager()


def _load_dream(llm, memory_manager):
    from .dream_distill import DreamDistillEngine

    return DreamDistillEngine(llm=llm, memory_manager=memory_manager)


# attachable subsystems (lazy-imported so they don't block startup)
def _load_goal(llm, project_root=None):
    from .goal import GoalJudgeEngine

    return GoalJudgeEngine(
        llm_agent=llm,
        llm_judge=None,
        project_root=project_root,
    )


def _load_skills():
    from .skills import SkillLoader

    loader = SkillLoader()
    tools = loader.discover()
    return loader, tools


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        *,
        auto_load_skills: bool = True,
        project_root: str | None = None,
    ):
        self.llm = llm
        self.messages: list[dict] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self._project_root = project_root

        # -- subsystems (set up before tools so skill-loader can inject) --
        self.memory_manager = _load_memory()
        self.dream_engine = _load_dream(llm, self.memory_manager)
        self.goal_engine = _load_goal(llm, project_root)

        # -- tools --------------------------------------------------------
        self.skill_loader: object = None
        skill_tools: list[Tool] = []
        if auto_load_skills:
            self.skill_loader, skill_tools = _load_skills()

        base_tools = tools if tools is not None else ALL_TOOLS
        # built-in tools take priority; skill names won't overwrite them
        known_names = {t.name for t in base_tools}
        for st in skill_tools:
            if st.name not in known_names:
                base_tools.append(st)
        self.tools = base_tools

        self._system = system_prompt(self.tools)

        # wire up sub-agent capability
        for t in self.tools:
            if isinstance(t, AgentTool):
                t._parent_agent = self

    def _full_messages(self) -> list[dict]:
        return [{"role": "system", "content": self._system}] + self.messages

    def _tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools]

    def chat(self, user_input: str, on_token=None, on_tool=None) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})

        # Auto-record user input as episodic memory
        self._record_user_input(user_input)

        self.context.maybe_compress(self.messages, self.llm)

        # Track tool calls in this round for pattern detection
        round_tool_calls: list[str] = []

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                # Auto-record the response as episodic memory
                self._record_assistant_response(resp.content)
                # Record tool pattern if meaningful
                if round_tool_calls:
                    self._record_tool_pattern(round_tool_calls)
                # Auto-trigger dream consolidation if conditions are met
                self._maybe_auto_dream()
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            tc_names = [tc.name for tc in resp.tool_calls]
            round_tool_calls.extend(tc_names)

            if len(resp.tool_calls) == 1:
                tc = resp.tool_calls[0]
                if on_tool:
                    on_tool(tc.name, tc.arguments)
                result = self._exec_tool(tc)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
            else:
                # parallel execution for multiple tool calls
                results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                for tc, result in zip(resp.tool_calls, results):
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    def _exec_tool(self, tc) -> str:
        """Execute a single tool call, returning the result string."""
        tool = get_tool(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        try:
            return tool.execute(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        except Exception as e:
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(self, tool_calls, on_tool=None) -> list[str]:
        """Run multiple tool calls concurrently using threads.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, run them in parallel.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self._exec_tool, tc) for tc in tool_calls]
            return [f.result() for f in futures]

    # ------------------------------------------------------------------
    #  Memory auto-recording helpers
    # ------------------------------------------------------------------

    def _record_user_input(self, user_input: str) -> None:
        """Record user input as episodic memory.

        Skips short inputs (likely commands) and technical noise.
        """
        if not self.memory_manager:
            return

        stripped = user_input.strip()
        # Skip very short inputs and slash-commands
        if len(stripped) < 5 or stripped.startswith("/"):
            return

        self.memory_manager.remember(
            content=stripped,
            type="episodic",
            importance=0.3,
            tags=["user_input"],
        )

    def _record_assistant_response(self, content: str) -> None:
        """Record meaningful assistant responses as episodic memory."""
        if not self.memory_manager or not content:
            return

        stripped = content.strip()
        if len(stripped) < 20:
            return

        # Only keep the first 500 chars as a summary
        summary = stripped[:500]
        self.memory_manager.remember(
            content=summary,
            type="episodic",
            importance=0.4,
            tags=["assistant_response"],
        )

    def _record_tool_pattern(self, tool_names: list[str]) -> None:
        """Record a sequence of tool calls as a procedural pattern.

        Only records when the sequence is long enough to be meaningful.
        """
        if not self.memory_manager or len(tool_names) < 3:
            return

        pattern = " -> ".join(tool_names)
        # Deduplicate: skip if we already have this exact pattern
        existing = self.memory_manager.recall(pattern, n=1, types="procedural")
        if existing and existing[0].content == pattern:
            # Bump access_count
            meta = existing[0].metadata
            meta["access_count"] = meta.get("access_count", 1) + 1
            return

        self.memory_manager.remember(
            content=pattern,
            type="procedural",
            importance=0.5,
            tags=["workflow", "tool_pattern"],
            metadata={"access_count": 1, "confidence": 0.3},
        )

    def _maybe_auto_dream(self) -> None:
        """Auto-trigger dream consolidation if the auto-trigger conditions are met."""
        if not self.memory_manager or not self.dream_engine:
            return
        try:
            if self.dream_engine.triggers.should_dream(self.memory_manager):
                self.dream_engine.dream()
        except Exception:
            pass  # silent fail for auto-trigger

    def reset(self):
        """Clear conversation history."""
        self.messages.clear()
