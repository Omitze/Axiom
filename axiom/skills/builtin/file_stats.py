"""File statistics tool — count lines, functions, classes in source files.

Provides a quick overview of a project's size and structure without
requiring external tools like ``cloc`` or ``tokei``.
"""

from pathlib import Path

from axiom.tools.base import Tool


class FileStatsTool(Tool):
    name = "file_stats"
    description = (
        "Get statistics about a file or directory. "
        "Returns: line count, character count, file count (for directories), "
        "and a breakdown by file extension."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to analyze",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str) -> str:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: {path} not found"

        if p.is_file():
            return self._file_stats(p)
        return self._dir_stats(p)

    @staticmethod
    def _file_stats(p: Path) -> str:
        text = p.read_text(errors="ignore")
        lines = text.splitlines()
        total_lines = len(lines)
        non_empty = sum(1 for ln in lines if ln.strip())
        chars = len(text)
        ext = p.suffix or "(no extension)"

        return (
            f"File: {p.name}\n"
            f"  Size: {p.stat().st_size:,} bytes\n"
            f"  Lines: {total_lines:,} total, {non_empty:,} non-empty\n"
            f"  Characters: {chars:,}\n"
            f"  Extension: {ext}"
        )

    @staticmethod
    def _dir_stats(p: Path) -> str:
        files = [f for f in p.rglob("*") if f.is_file()]
        total = len(files)

        # Skip .git and other junk
        files = [
            f
            for f in files
            if not any(part.startswith(".") for part in f.relative_to(p).parts)
        ]

        # Extension breakdown
        ext_counts: dict[str, int] = {}
        ext_lines: dict[str, int] = {}
        for f in files:
            ext = f.suffix or "(no ext)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            try:
                text = f.read_text(errors="ignore")
                ext_lines[ext] = ext_lines.get(ext, 0) + len(text.splitlines())
            except Exception:
                pass

        ext_breakdown = "\n".join(
            f"  {ext or '(none)'}: {ext_counts[ext]} files, "
            f"{ext_lines.get(ext, 0):,} lines"
            for ext in sorted(ext_counts, key=lambda e: -ext_counts[e])[:15]
        )

        total_lines = sum(ext_lines.values())

        return (
            f"Directory: {p.name}\n"
            f"  Total files: {total:,}\n"
            f"  Total lines: {total_lines:,}\n"
            f"  By extension:\n{ext_breakdown}"
        )


def create_tool() -> Tool:
    return FileStatsTool()
