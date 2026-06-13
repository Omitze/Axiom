"""Interactive REPL - the user-facing terminal interface."""

import argparse
import os
import sys

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from . import __version__
from .agent import Agent
from .config import Config
from .goal import GoalJudgeEngine
from .llm import LLM, LiteLLM
from .session import list_sessions, load_session, save_session

console = Console()


def _parse_args():
    p = argparse.ArgumentParser(
        prog="axiom",
        description="Axiom - LLM-powered autonomous coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $AXIOM_MODEL or gpt-4o)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return p.parse_args()


def main():
    args = _parse_args()
    config = Config.from_env()

    # CLI args override env vars
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or AXIOM_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 AXIOM_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )
    agent = Agent(llm=llm, max_context_tokens=config.max_context_tokens)

    agent.goal_engine = GoalJudgeEngine(
        llm_agent=llm,
        llm_judge=None,  # same model by default; users can customise
        project_root=os.getcwd(),
    )

    # resume saved session
    if args.resume:
        loaded = load_session(args.resume)
        if loaded:
            agent.messages, loaded_model = loaded
            # restore the model from the saved session unless overridden by CLI
            if not args.model:
                agent.llm.model = loaded_model
                config.model = loaded_model
            console.print(
                f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]"
            )
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # interactive REPL
    _repl(agent, config)


def _run_once(agent: Agent, prompt: str):
    """Non-interactive: run one prompt and exit."""

    def on_token(tok):
        print(tok, end="", flush=True)

    def on_tool(name, kwargs):
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    print()


def _repl(agent: Agent, config: Config):
    """Interactive read-eval-print loop."""
    console.print(
        Panel(
            f"[bold]Axiom[/bold] v{__version__}\n"
            f"Model: [cyan]{config.model}[/cyan]"
            + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
            + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
            border_style="blue",
        )
    )

    hist_path = os.path.expanduser("~/.axiom_history")
    history = FileHistory(hist_path)

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            user_input = pt_prompt(
                "You > ",
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p + c}[/bold] total"
            cost = agent.llm.estimated_cost
            if cost is not None:
                line += f"  (~${cost:.4f})"
            console.print(line)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            new_model = (
                user_input[7:].strip() if user_input.startswith("/model ") else ""
            )
            if new_model:
                agent.llm.model = new_model
                config.model = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            else:
                console.print(f"Current model: [cyan]{config.model}[/cyan]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens

            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(
                    f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]"
                )
            else:
                console.print(
                    f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]"
                )
            continue
        if user_input == "/save":
            sid = save_session(agent.messages, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: axiom -r {sid}")
            continue
        if user_input == "/diff":
            from .tools.edit import _changed_files

            if not _changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(
                    f"[bold]Files modified this session ({len(_changed_files)}):[/bold]"
                )
                for f in sorted(_changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    console.print(
                        f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}"
                    )
            continue

        # ------------------------------------------------------------------
        #  Dream command
        # ------------------------------------------------------------------

        if user_input == "/dream":
            if not agent.memory_manager:
                console.print("[red]Memory system not available.[/red]")
                continue
            console.print("[dim]Running dream (memory consolidation)...[/dim]")
            try:
                report = agent.dream_engine.dream()
                if report:
                    console.print(f"[green]Dream complete:[/green] {report.summary}")
                else:
                    console.print("[dim]No memories to consolidate.[/dim]")
            except Exception as e:
                console.print(f"[red]Dream failed: {e}[/red]")
            continue

        # ------------------------------------------------------------------
        #  Distill command
        # ------------------------------------------------------------------

        if user_input == "/distill":
            if not agent.memory_manager:
                console.print("[red]Memory system not available.[/red]")
                continue
            console.print("[dim]Running workflow distillation...[/dim]")
            try:
                # use saved sessions as source data for mining
                sessions_list = list_sessions()
                session_data = []
                for s in sessions_list[:20]:  # last 20 sessions max
                    loaded = load_session(s["id"])
                    if loaded:
                        msgs, _ = loaded
                        session_data.append({"messages": msgs})

                result = agent.dream_engine.distill(sessions=session_data)
                agent.dream_engine._last_distill_result = result
                if result.patterns:
                    console.print(
                        f"[green]Distilled {len(result.patterns)} pattern(s):[/green]"
                    )
                    for p in result.patterns[:10]:
                        conf = f"conf={p.confidence:.0%}"
                        console.print(
                            f"  • [cyan]{p.name}[/cyan] "
                            f"({p.frequency}x, {conf}, {len(p.steps)} steps)"
                        )
                    if result.high_confidence:
                        console.print(
                            f"[green]✓ {len(result.high_confidence)} high-confidence "
                            f"pattern(s) packaged as skills.[/green]"
                        )
                    if result.generated_skills:
                        for skill in result.generated_skills:
                            console.print(f"   📦 {skill}")
                    if result.medium_confidence:
                        console.print(
                            f"[yellow]~ {len(result.medium_confidence)} medium-confidence "
                            f"pattern(s) — use '/distill approve <name>' to package.[/yellow]"
                        )
                else:
                    console.print("[dim]No workflow patterns found.[/dim]")
            except Exception as e:
                console.print(f"[red]Distill failed: {e}[/red]")
            continue

        if user_input.startswith("/distill approve "):
            name = user_input[17:].strip()
            result = getattr(agent.dream_engine, "_last_distill_result", None)
            if result is None:
                console.print("[yellow]Run /distill first.[/yellow]")
                continue
            if agent.dream_engine.approve(name, result):
                console.print(
                    f"[green]Approved '{name}' and packaged as a skill.[/green]"
                )
            else:
                console.print(
                    f"[yellow]Pattern '{name}' not found or already approved.[/yellow]"
                )
            continue

        # ------------------------------------------------------------------
        #  Memory commands
        # ------------------------------------------------------------------

        if user_input == "/memory":
            if not agent.memory_manager:
                console.print("[red]Memory system not available.[/red]")
                continue
            try:
                from .memory import MemoryType

                s = agent.memory_manager.summary()
                lines = [f"[bold]Memory:[/bold] {s['total']} items"]
                if s["total"] > 0:
                    for t, cnt in s["by_type"].items():
                        lines.append(f"  • {t}: {cnt}")
                    lines.append(f"  • Avg importance: {s['avg_importance']:.2f}")
                    lines.append(f"  • Storage: {s['storage_dir']}")
                    console.print(Panel("\n".join(lines), border_style="dim"))
                else:
                    console.print("[dim]No memories yet.[/dim]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
            continue

        if user_input.startswith("/memory "):
            if not agent.memory_manager:
                console.print("[red]Memory system not available.[/red]")
                continue
            query = user_input[8:].strip()
            results = agent.memory_manager.recall(query, n=8)
            if results:
                console.print(f"[bold]Recall:[/bold] {len(results)} result(s)")
                for r in results:
                    t = r.type.value if hasattr(r.type, "value") else r.type
                    console.print(
                        f"  • [dim][{t}][/dim] {r.content[:120]}"
                        f"{'...' if len(r.content) > 120 else ''}"
                    )
            else:
                console.print("[dim]No matching memories found.[/dim]")
            continue

        # ------------------------------------------------------------------
        #  Skills command
        # ------------------------------------------------------------------

        if user_input == "/skills":
            if agent.skill_loader and hasattr(agent.skill_loader, "registry"):
                reg = agent.skill_loader.registry
                tools = reg.list()
                if tools:
                    console.print(f"[bold]Skills loaded:[/bold] {len(tools)}")
                    for t in tools:
                        console.print(f"  • [cyan]{t.name}[/cyan] — {t.description}")
                else:
                    console.print("[dim]No skills loaded from disk.[/dim]")
            else:
                # fallback: list all tools (including builtins)
                console.print(f"[bold]Agent tools:[/bold] {len(agent.tools)}")
                for t in agent.tools:
                    console.print(f"  • [cyan]{t.name}[/cyan] — {t.description}")
            continue

        # ------------------------------------------------------------------
        #  Code Analysis command
        # ------------------------------------------------------------------

        if user_input == "/analyze" or user_input.startswith("/analyze "):
            from .code_analysis import ProjectAnalyzer, format_report

            path = user_input[9:].strip() if user_input.startswith("/analyze ") else "."
            console.print(f"[dim]Analyzing {path}...[/dim]")
            try:
                analyzer = ProjectAnalyzer()
                result = analyzer.analyze(path)
                report = format_report(result)
                console.print(Markdown(report))
            except FileNotFoundError:
                console.print(f"[red]Path not found: {path}[/red]")
            except NotADirectoryError:
                console.print(f"[red]Expected a directory, got: {path}[/red]")
            except Exception as e:
                console.print(f"[red]Analysis failed: {e}[/red]")
            continue

        # ------------------------------------------------------------------
        #  Goal & Judge commands
        # ------------------------------------------------------------------

        if user_input.startswith("/goal "):
            goal_text = user_input[6:].strip()
            # auto-refine if it looks vague (short descriptions)
            refine = len(goal_text.split()) < 6
            if not hasattr(agent, "goal_engine"):
                console.print("[red]Goal & Judge system not loaded.[/red]")
                continue
            goal = agent.goal_engine.set_goal(goal_text, refine=False)
            console.print(f"[green]Goal set:[/green] {goal.description}")
            if goal.criteria:
                for i, c in enumerate(goal.criteria, 1):
                    console.print(f"  {i}. {c}")
            if refine:
                console.print(
                    "[dim]Tip: Goal looks short — try '/goal refine' to decompose into sub-conditions.[/dim]"
                )
            continue

        if user_input == "/goal":
            if not hasattr(agent, "goal_engine"):
                console.print("[red]Goal & Judge system not loaded.[/red]")
                continue
            s = agent.goal_engine.summary()
            if s:
                console.print(f"[bold]Current Goal:[/bold] {s['description']}")
                console.print(f"Criteria ({s['criteria_count']}):")
                for c in s["criteria"]:
                    console.print(f"  • {c}")
                if s["pinned"]:
                    console.print("[dim](pinned — cannot be auto-modified)[/dim]")
            else:
                console.print("[dim]No goal set. Use /goal <description>[/dim]")
            continue

        if user_input == "/goal clear":
            if not hasattr(agent, "goal_engine"):
                console.print("[red]Goal & Judge system not loaded.[/red]")
                continue
            agent.goal_engine.clear()
            console.print("[yellow]Goal cleared.[/yellow]")
            continue

        if user_input == "/goal refine":
            if not hasattr(agent, "goal_engine"):
                console.print("[red]Goal & Judge system not loaded.[/red]")
                continue
            current = agent.goal_engine.goal_manager.current_goal
            if current is None:
                console.print(
                    "[yellow]No goal to refine. Set one first with /goal <description>[/yellow]"
                )
                continue
            if current.pinned:
                console.print("[yellow]Goal is pinned and cannot be refined.[/yellow]")
                continue
            console.print("[dim]Refining goal...[/dim]")
            refined = agent.goal_engine.refine_goal()
            if refined:
                console.print(f"[green]Refined goal:[/green] {refined.description}")
                for i, c in enumerate(refined.criteria, 1):
                    console.print(f"  {i}. {c}")
            continue

        if user_input == "/judge":
            if not hasattr(agent, "goal_engine"):
                console.print("[red]Goal & Judge system not loaded.[/red]")
                continue
            if agent.goal_engine.goal_manager.current_goal is None:
                console.print(
                    "[yellow]No goal set. Use /goal <description> first.[/yellow]"
                )
                continue
            console.print("[dim]Evaluating goal against conversation...[/dim]")
            verdict = agent.goal_engine.judge_with_verifier(conversation=agent.messages)
            if verdict.goal_met:
                console.print(
                    f"[green]✓ Goal met! ({verdict.vote:.0%} confidence)[/green]"
                )
            else:
                console.print(
                    f"[yellow]✗ Goal not met ({verdict.vote:.0%} confidence):[/yellow]"
                )
            if verdict.evidence:
                console.print(
                    Panel(
                        "\n".join(f"  • {e}" for e in verdict.evidence[:5]),
                        title="Evidence",
                        border_style="green",
                    )
                )
            if verdict.gaps:
                console.print(
                    Panel(
                        "\n".join(f"  ❌ {g}" for g in verdict.gaps[:5]),
                        title="Gaps",
                        border_style="red",
                    )
                )
            if verdict.suggested_fix and not verdict.goal_met:
                console.print(f"[bold]Suggestion:[/bold] {verdict.suggested_fix}")
            continue

        # call the agent
        streamed: list[str] = []

        def on_token(tok):
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name, kwargs):
            console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
            if streamed:
                print()  # newline after streamed tokens
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


def _show_help():
    help_text = (
        "[bold]General:[/bold]\n"
        "  /help             Show this help\n"
        "  /reset            Clear conversation history\n"
        "  /model            Show current model\n"
        "  /model <name>     Switch model mid-conversation\n"
        "  /tokens           Show token usage\n"
        "  /compact          Compress conversation context\n"
        "  /save             Save session to disk\n"
        "  /sessions         List saved sessions\n"
        "  /diff             Show files modified this session\n"
        "\n"
        "[bold]Cognitive:[/bold]\n"
        "  /memory           Show memory statistics\n"
        "  /memory <query>   Search memories\n"
        "  /dream            Run memory consolidation (dream)\n"
        "  /distill          Mine & package workflow skills\n"
        "  /distill approve <name>  Approve a medium-confidence pattern\n"
        "\n"
        "[bold]Analysis:[/bold]\n"
        "  /analyze [path]   AST-based code analysis\n"
        "  /skills           List loaded skills & tools\n"
        "\n"
        "[bold]Task:[/bold]\n"
        "  /goal             Show current goal\n"
        "  /goal <desc>      Set a task completion goal\n"
        "  /goal refine      Decompose goal into checkable sub-conditions\n"
        "  /goal clear       Clear the current goal\n"
        "  /judge            Evaluate whether the goal is truly met\n"
        "\n"
        "[bold]Session:[/bold]\n"
        "  quit              Exit Axiom\n"
        "\n"
        "[bold]Input:[/bold]\n"
        "  Enter             Submit message\n"
        "  Esc+Enter         Insert newline (for pasting code)"
    )
    console.print(Panel(help_text, title="Axiom Help", border_style="dim"))


def _brief(kwargs: dict, maxlen: int = 80) -> str:
    s = ", ".join(f"{k}={repr(v)[:40]}" for k, v in kwargs.items())
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
