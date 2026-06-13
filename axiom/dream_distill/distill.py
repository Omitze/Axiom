"""Workflow distillation — the "distill" phase.

Given session histories, the distill cycle:

1. Extract raw tool-call sequences from recent sessions.
2. Mine frequent sub-sequences with the PrefixSpan algorithm.
3. Ask the LLM to name and generalise each candidate.
4. Package high-confidence candidates as loadable skills.

This module can operate **without an LLM**: it will still mine patterns
and assign numeric names — it just won't produce the rich semantic
labelling and generalisation.
"""

from __future__ import annotations

import json
import math
import textwrap
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from .schemas import DistillResult, WorkflowPattern

if TYPE_CHECKING:
    from axiom.llm import LLM
    from axiom.memory import MemoryManager

# ---------------------------------------------------------------------------
#  PrefixSpan — frequent sub-sequence mining
# ---------------------------------------------------------------------------


def prefixspan_mine(
    sequences: list[list[str]],
    min_support: int = 3,
    max_pattern_len: int = 10,
) -> list[list[str]]:
    """Mine frequent sub-sequences from a list of tool-call sequences.

    Parameters
    ----------
    sequences:
        Each entry is an ordered list of tool names, e.g.
        ``["read_file", "edit_file", "bash"]``.
    min_support:
        Minimum number of sequences a pattern must appear in.
    max_pattern_len:
        Maximum length of a mined pattern.

    Returns
    -------
    list[list[str]]
        Frequent sub-sequences, ordered by frequency descending
        (most common first).

    Example
    -------
    >>> seqs = [
    ...     ["read_file", "edit_file", "bash"],
    ...     ["read_file", "edit_file", "write_file"],
    ...     ["read_file", "edit_file", "bash"],
    ...     ["grep", "read_file"],
    ... ]
    >>> prefixspan_mine(seqs, min_support=3)
    [["read_file", "edit_file"]]

    Complexity: O(N * L^2) where N = number of sequences, L = avg length.
    """
    if not sequences or min_support < 1:
        return []

    result: list[tuple[list[str], int]] = []
    _prefixspan_grow([], sequences, min_support, max_pattern_len, result)

    # Deduplicate and sort by frequency descending
    seen: set[str] = set()
    unique: list[tuple[list[str], int]] = []
    for pattern, sup in result:
        key = "|".join(pattern)
        if key not in seen:
            seen.add(key)
            unique.append((pattern, sup))

    unique.sort(key=lambda x: (-x[1], len(x[0])))
    return [p for p, _ in unique]


def _prefixspan_grow(
    prefix: list[str],
    projected: list[list[str]],
    min_support: int,
    max_len: int,
    result: list[tuple[list[str], int]],
):
    """Recursive growth step of the PrefixSpan algorithm."""
    if len(prefix) >= max_len:
        return

    # Count items that appear in each projected sequence (at most once per seq)
    freq: Counter[str] = Counter()
    for seq in projected:
        seen_in_seq: set[str] = set()
        # For non-empty prefix, scan from the position where prefix matched
        for item in seq:
            if item not in seen_in_seq:
                freq[item] += 1
                seen_in_seq.add(item)

    for item, count in freq.most_common():
        if count < min_support:
            continue

        new_prefix = prefix + [item]
        result.append((new_prefix, count))

        # Build projected DB for next level
        new_projected = []
        for seq in projected:
            # Find first occurrence of item
            try:
                idx = seq.index(item)
            except ValueError:
                continue
            # The remainder after item
            suffix = seq[idx + 1 :]
            if suffix:
                new_projected.append(suffix)

        if new_projected:
            _prefixspan_grow(new_prefix, new_projected, min_support, max_len, result)


# ---------------------------------------------------------------------------
#  PatternMiner
# ---------------------------------------------------------------------------


def _extract_tool_sequences(
    sessions: list[dict],
) -> list[list[str]]:
    """Extract ordered tool-call names from serialised session dicts.

    Each session dict is expected to have a ``"tools"`` or ``"messages"``
    key whose value is a list of tool-call entries.
    """
    sequences: list[list[str]] = []
    for session in sessions:
        if not isinstance(session, dict):
            continue

        tools: list[str] = []

        # Try "tools" key (our distillation format)
        raw_tools = session.get("tools")
        if isinstance(raw_tools, list):
            for step in raw_tools:
                if isinstance(step, dict) and "tool" in step:
                    tools.append(str(step["tool"]))
                elif isinstance(step, str):
                    tools.append(step)

        # Fallback: extract from messages
        if not tools:
            raw_messages = session.get("messages")
            if isinstance(raw_messages, list):
                for msg in raw_messages:
                    if not isinstance(msg, dict):
                        continue
                    tc = msg.get("tool_calls", [])
                    if tc:
                        for call in tc:
                            if isinstance(call, dict):
                                tools.append(call.get("function", {}).get("name", "?"))
                            else:
                                tools.append(str(call))

        if tools:
            sequences.append(tools)

    return sequences


class PatternMiner:
    """Mine frequent workflow patterns from session histories.

    Parameters
    ----------
    llm:
        Optional LLM for semantic naming and generalisation.
    min_support:
        Minimum occurrence count for a pattern to be considered.
    confidence_threshold:
        Patterns with confidence above this are **high confidence**.
    confidence_mid:
        Patterns above this (but below high) are **medium confidence**.
    """

    _EVAL_PROMPT = (
        "You are a workflow-analysis expert.  I will show you a pattern of "
        "tool calls that appears repeatedly in a coding assistant's logs.\n\n"
        "Pattern: {steps}\n"
        "Frequency: {frequency}\n\n"
        "Please respond with a JSON object:\n"
        "{{\n"
        '  "name": "short-kebab-name-for-this-pattern",\n'
        '  "description": "one-line description",\n'
        '  "contexts": ["typical trigger scenario 1", "scenario 2"],\n'
        '  "candidate_type": "skill" | "subagent" | "alias"\n'
        "}}\n"
        "Return ONLY the JSON object, no other text."
    )

    def __init__(
        self,
        llm: LLM | None = None,
        min_support: int = 3,
        confidence_threshold: float = 0.8,
        confidence_mid: float = 0.5,
    ):
        self.llm = llm
        self.min_support = min_support
        self.confidence_threshold = confidence_threshold
        self.confidence_mid = confidence_mid

    def mine(
        self,
        sessions: list[dict],
        memory_manager: MemoryManager | None = None,
    ) -> DistillResult:
        """Run one distillation pass.

        Parameters
        ----------
        sessions:
            List of session dicts (as returned by :func:`session.list_sessions`
            or loaded via :func:`session.load_session`).
        memory_manager:
            Optional memory store — used to enrich context.

        Returns
        -------
        DistillResult
            Discovered patterns, sorted into confidence bands.
        """
        # 1. Extract tool sequences
        sequences = _extract_tool_sequences(sessions)
        if not sequences:
            return DistillResult()

        # 2. Mine frequent sub-sequences
        raw_patterns = prefixspan_mine(sequences, min_support=self.min_support)
        if not raw_patterns:
            return DistillResult()

        # 3. Convert to WorkflowPattern objects
        total_seqs = len(sequences)
        candidates: list[WorkflowPattern] = []
        for pattern in raw_patterns[:10]:  # cap at 10 candidates
            steps_list = [
                {"tool": step, "args_summary": "...", "duration": 0.0}
                for step in pattern
            ]

            steps_str = " -> ".join(pattern)
            seq_count = _count_occurrences(sequences, pattern)
            confidence = _compute_confidence(
                frequency=seq_count,
                total_sequences=total_seqs,
                pattern_length=len(pattern),
            )

            wp = WorkflowPattern(
                name=f"pattern-{'-'.join(pattern)}",
                steps=steps_list,
                frequency=seq_count,
                confidence=confidence,
                avg_duration=0.0,
                result_quality=0.5,
                candidate_type="skill",
            )

            # LLM enrichment (optional)
            if self.llm is not None:
                self._enrich_with_llm(wp, steps_str)

            candidates.append(wp)

        # 4. Split into confidence bands
        result = DistillResult(patterns=candidates)
        for p in candidates:
            if p.confidence >= self.confidence_threshold:
                result.high_confidence.append(p)
            elif p.confidence >= self.confidence_mid:
                result.medium_confidence.append(p)

        return result

    # -- internal helpers ---------------------------------------------------

    def _enrich_with_llm(self, wp: WorkflowPattern, steps_str: str) -> None:
        """Use the LLM to give the pattern a semantic name and context."""
        prompt = self._EVAL_PROMPT.format(
            steps=steps_str,
            frequency=wp.frequency,
        )
        try:
            resp = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content.strip()
            # Extract JSON object
            start = text.find("{")
            end = text.rfind("}") + 1
            if 0 <= start < end:
                data = json.loads(text[start:end])
                if "name" in data:
                    wp.name = data["name"]
                if "contexts" in data:
                    wp.contexts = data["contexts"]
                if "candidate_type" in data:
                    wp.candidate_type = data["candidate_type"]
        except Exception:
            pass  # graceful fallback — keep auto-generated name


# ---------------------------------------------------------------------------
#  SkillPackager
# ---------------------------------------------------------------------------


class SkillPackager:
    """Package a :class:`WorkflowPattern` as a loadable skill file.

    The generated skill is written to ``~/.axiom/skills/{name}/``
    and can be loaded by the :class:`SkillLoader` on next startup.
    """

    SKILL_TEMPLATE = '''\
"""Skill auto-generated by Dream & Distill — {description}"""

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
        """Execute the {name} workflow."""
{body}


def create_tool() -> Tool:
    """Factory called by the SkillLoader."""
    return {class_name}()
'''

    def __init__(self, output_dir: str | Path | None = None):
        if output_dir is None:
            output_dir = Path.home() / ".axiom" / "skills"
        self.output_dir = Path(output_dir)

    def package(
        self,
        pattern: WorkflowPattern,
        llm: LLM | None = None,
    ) -> str | None:
        """Generate a skill file from a workflow pattern.

        Parameters
        ----------
        pattern:
            The mined pattern to package.
        llm:
            Optional LLM used to generate the implementation body.
            If ``None``, a simple stub is used.

        Returns
        -------
        str | None
            The generated source code, or ``None`` if packaging failed.
        """
        name = pattern.name.replace("/", "_").replace(" ", "_").lower()
        class_name = self._to_class_name(name)
        description = f"Auto-generated skill for {pattern.name}"

        # Generate the body — either from the LLM or a simple loop stub
        if llm is not None and pattern.steps:
            body = self._generate_body_with_llm(pattern, llm)
        else:
            body = self._default_body(pattern)

        properties = {
            "workflow": {"type": "string", "description": "Workflow step to execute"}
        }
        required = ["workflow"]

        code = self.SKILL_TEMPLATE.format(
            name=name,
            class_name=class_name,
            description=description,
            properties=json.dumps(properties, indent=8),
            required=json.dumps(required),
            params="workflow: str = 'all'",
            body=textwrap.indent(body, " " * 12),
        )

        # Write to disk
        skill_dir = self.output_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "__init__.py").write_text(f"from .{name} import create_tool\n")
        (skill_dir / f"{name}.py").write_text(code)

        return code

    @staticmethod
    def _to_class_name(name: str) -> str:
        """Convert snake_case/kebab name to PascalCase."""
        parts = name.replace("-", "_").split("_")
        return "".join(p.capitalize() for p in parts) + "Tool"

    @staticmethod
    def _default_body(pattern: WorkflowPattern) -> str:
        """Generate a default execute body that loops over steps."""
        step_names = [s.get("tool", "?") for s in pattern.steps]
        if not step_names:
            return 'return "No steps defined for this pattern"'
        steps_repr = repr(step_names)
        return (
            '"""Run the {name} workflow."""\n'
            "results = []\n"
            "for step in {steps}:\n"
            '    results.append(f"Executing {{step}}...")\n'
            'return "\\n".join(results)'
        ).format(name=pattern.name, steps=steps_repr)

    def _generate_body_with_llm(self, pattern: WorkflowPattern, llm: LLM) -> str:
        """Ask the LLM to generate the skill implementation."""
        prompt = (
            "Generate the body of an execute() method for a tool that "
            "implements the following multi-step workflow. Return ONLY "
            "raw Python code, no markdown or explanation.\n\n"
            f"Workflow: {pattern.name}\n"
            f"Steps: {json.dumps(pattern.steps, indent=2)}\n"
            "The method receives a single parameter `workflow: str` "
            "(default 'all')."
        )
        try:
            resp = llm.chat(messages=[{"role": "user", "content": prompt}])
            code = resp.content.strip()
            # Strip markdown fences if present
            if code.startswith("```"):
                code = code.split("\n", 1)[-1]
                code = code.rsplit("```", 1)[0]
            return code.strip()
        except Exception:
            return self._default_body(pattern)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------


def _count_occurrences(sequences: list[list[str]], pattern: list[str]) -> int:
    """Count how many sequences contain the pattern as a sub-sequence.

    Uses the iterator ``in`` operator which scans forward through the
    sequence to find each step, effectively checking subsequence membership
    in O(N) per sequence.
    """
    count = 0
    for seq in sequences:
        # Check if pattern appears as a sub-sequence (not necessarily contiguous)
        it = iter(seq)
        if all(step in it for step in pattern):
            count += 1
    return count


def _compute_confidence(
    frequency: int,
    total_sequences: int,
    pattern_length: int,
    consistency: float = 1.0,
    quality: float = 0.5,
) -> float:
    """Composite confidence score.

    Formula::

        confidence = freq_ratio * sigmoid(length) * consistency * quality

    Where:
    - ``freq_ratio = frequency / total_sequences``
    - ``sigmoid(length) = 1 / (1 + exp(-(length - 2)))`` — longer
      patterns are more interesting, capped gently.
    """
    freq_ratio = frequency / max(total_sequences, 1)
    length_bonus = 1.0 / (1.0 + math.exp(-(pattern_length - 2)))
    return min(1.0, freq_ratio * length_bonus * consistency * quality)
