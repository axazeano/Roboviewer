"""Read-only tools for the reviewer agent.

Deliberately nothing that writes to disk or runs arbitrary commands: the reviewer
must not edit the code it is reviewing. Every path is confined to the repository
root.

Everything is read from git by the reviewed branch's ref rather than from the
working copy. The review may target a branch that is not checked out, and one
that is may carry uncommitted edits that would shift line numbers away from what
the agent sees in the attached files.

The descriptions and the returned text below go to the model, so they are part
of the prompt surface: they say what the tool is for, not just what it does.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .gitdiff import looks_binary, show_file_at

MAX_TOOL_OUTPUT = 60_000
# Cheap early-out for the obvious cases; anything else is caught by inspecting the
# content, so no stack-specific suffixes are needed here.
_TEXT_SUFFIX_BLOCKLIST = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".zip", ".gz", ".tar", ".mp4", ".mov", ".mp3", ".ttf", ".otf", ".woff", ".woff2",
}


class ToolError(Exception):
    pass


def _safe_path(root: Path, rel: str) -> Path:
    candidate = (root / rel.lstrip("/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise ToolError(f"Path outside the repository is not allowed: {rel}") from None
    return candidate


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    cut = f"\n\n[... output truncated to {MAX_TOOL_OUTPUT} characters ...]"
    return text[:MAX_TOOL_OUTPUT] + cut


# --------------------------------------------------------------------------- tools


def _relative(root: Path, path: str) -> str:
    return _safe_path(root, path).relative_to(root.resolve()).as_posix()


def _line_number(value: Any) -> int | None:
    """A line number as the model actually sends it.

    JSON arguments come back as whatever the model felt like emitting — `264`,
    `"264"`, `264.0`, `""` — and arithmetic on a string is a TypeError deep
    inside a tool call. Anything unusable becomes None, which every caller
    already treats as "not given".
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        # Through float, so `264.0` and `"264.0"` land on the same line as `264`
        number = int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def read_file(root: Path, path: str, start_line: Any = None, end_line: Any = None,
              max_lines: int = 800, ref: str = "HEAD") -> str:
    target = _safe_path(root, path)
    if target.suffix.lower() in _TEXT_SUFFIX_BLOCKLIST:
        raise ToolError(f"Binary file, reading is not supported: {path}")

    rel = target.relative_to(root.resolve()).as_posix()
    content = show_file_at(root, ref, rel)
    if content is None:
        raise ToolError(f"File not found on the reviewed branch: {path}")
    if looks_binary(content):
        raise ToolError(f"Binary file, reading is not supported: {path}")

    lines = content.splitlines()
    first, last = _line_number(start_line), _line_number(end_line)
    start = first or 1
    end = min(len(lines), last or start + max_lines - 1)
    if end - start + 1 > max_lines:
        end = start + max_lines - 1

    chunk = lines[start - 1 : end]
    numbered = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(chunk))
    header = f"{path} (lines {start}-{min(end, len(lines))} of {len(lines)})"
    return _truncate(f"{header}\n{numbered}")


def grep(root: Path, pattern: str, glob: str | None = None, ref: str = "HEAD",
         max_results: int = 60) -> str:
    # git grep, not ripgrep: we search the reviewed branch's tree, not the disk
    args = ["git", "grep", "-n", "-I", "-E", "--no-color", "-e", pattern, ref]
    if glob:
        args += ["--", glob]

    proc = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=60)
    if proc.returncode not in (0, 1):
        raise ToolError(f"git grep failed: {proc.stderr.strip()[:500]}")

    prefix = f"{ref}:"
    lines = [
        ln[len(prefix):] if ln.startswith(prefix) else ln
        for ln in proc.stdout.splitlines()
        if ln.strip()
    ]
    if not lines:
        return f"No matches for: {pattern}"
    shown = lines[:max_results]
    hidden = len(lines) - len(shown)
    suffix = f"\n[... {hidden} more matches ...]" if hidden else ""
    return _truncate("\n".join(shown) + suffix)


def list_files(root: Path, directory: str = ".", ref: str = "HEAD", max_entries: int = 200) -> str:
    rel = _relative(root, directory)
    spec = f"{ref}:{rel}" if rel not in ("", ".") else f"{ref}:"
    proc = subprocess.run(["git", "ls-tree", spec], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(f"Directory not found on the reviewed branch: {directory}")

    entries: list[str] = []
    for line in proc.stdout.splitlines():
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) < 2 or not name:
            continue
        entries.append(f"{name}/" if parts[1] == "tree" else name)
        if len(entries) >= max_entries:
            entries.append("[...]")
            break
    return "\n".join(sorted(entries)) or "(empty)"


def git_show(root: Path, path: str, ref: str) -> str:
    rel = _relative(root, path)
    content = show_file_at(root, ref, rel)
    if content is None:
        raise ToolError(
            f"Could not read {path} at revision {ref} "
            "(the file may have been created on this branch)"
        )
    if looks_binary(content):
        raise ToolError(f"Binary file, reading is not supported: {path}")
    lines = content.splitlines()
    numbered = "\n".join(f"{i + 1:>6}\t{line}" for i, line in enumerate(lines[:800]))
    return _truncate(f"{path} @ {ref} ({len(lines)} lines)\n{numbered}")


# --------------------------------------------------------------------------- schemas


def tool_schemas(base_ref: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a file in its new version (at the tip of the reviewed branch), with "
                    "line numbers. Use it for files not attached to the task in full, and for any "
                    "other code in the repository: callers, neighbouring files, tests."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path relative to the repository root",
                        },
                        "start_line": {"type": "integer", "description": "First line, 1-based"},
                        "end_line": {"type": "integer", "description": "Last line, inclusive"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": (
                    "Search the whole repository on the reviewed branch with an extended regular "
                    "expression. Use it to find callers, tests, duplicates and existing patterns — "
                    "and to confirm something is really missing before you report it as missing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Extended regular expression"},
                        "glob": {
                            "type": "string",
                            "description": "Restrict to a file mask, e.g. *.py or src/**",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": (
                    "List the files and subdirectories of a repository directory on the reviewed "
                    "branch."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Path relative to the repository root",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_show",
                "description": (
                    f"Read a file as it was BEFORE the changes (revision {base_ref[:12]}). "
                    "Use it when you need to compare the old and the new implementation in full."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    ]


SUBMIT_FINDINGS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_findings",
        "description": (
            "Finish the review and return the findings. Call this exactly once, at the end. "
            "An empty findings list is a normal result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "1-3 sentences: what was checked, and the overall conclusion for this "
                        "aspect."
                    ),
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {
                                "type": "string",
                                "description": "File path relative to the repository root",
                            },
                            "line": {
                                "type": "integer",
                                "description": "Line number in the NEW version of the file",
                            },
                            "end_line": {"type": "integer"},
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "major", "minor", "nit"],
                            },
                            "category": {
                                "type": "string",
                                "description": "A short slug, e.g. null-safety, race, api-break",
                            },
                            "title": {"type": "string", "description": "The problem in one line"},
                            "rationale": {
                                "type": "string",
                                "description": (
                                    "Why this is a problem, and the scenario "
                                    "in which it shows up"
                                ),
                            },
                            "suggestion": {
                                "type": "string",
                                "description": "A concrete suggested fix",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence from 0 to 1",
                            },
                        },
                        "required": [
                            "file", "severity", "category", "title", "rationale", "confidence",
                        ],
                    },
                },
            },
            "required": ["summary", "findings"],
        },
    },
}


SUBMIT_VERDICTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_verdicts",
        "description": "Finish judging and return a verdict for every finding.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Overall assessment of the merge request, 2-5 sentences.",
                },
                "verdicts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "finding_id": {"type": "string"},
                            "verdict": {
                                "type": "string",
                                "enum": ["confirmed", "false_positive", "nitpick", "duplicate"],
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "major", "minor", "nit"],
                                "description": "Corrected severity, when the original one is wrong",
                            },
                            "reason": {
                                "type": "string",
                                "description": "A short justification for the verdict",
                            },
                        },
                        "required": ["finding_id", "verdict", "reason"],
                    },
                },
            },
            "required": ["summary", "verdicts"],
        },
    },
}


SUBMIT_VERDICT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_verdict",
        "description": "Finish judging and return the verdict on the one finding you were given.",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["confirmed", "false_positive", "nitpick", "duplicate"],
                },
                "severity": {
                    "type": "string",
                    "enum": ["blocker", "major", "minor", "nit"],
                    "description": "Corrected severity, when the original one is wrong",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "One or two sentences: what you checked and what it showed. "
                        "Name the file and line you verified against."
                    ),
                },
            },
            "required": ["verdict", "reason"],
        },
    },
}


def dispatch(root: Path, name: str, args: dict[str, Any], *, base_ref: str, head_ref: str,
             max_read_lines: int) -> str:
    """Run a tool. Errors go back to the agent as text so it can correct itself."""
    try:
        if name == "read_file":
            return read_file(
                root,
                str(args["path"]),
                args.get("start_line"),
                args.get("end_line"),
                max_lines=max_read_lines,
                ref=head_ref,
            )
        if name == "grep":
            return grep(root, str(args["pattern"]), args.get("glob"), ref=head_ref)
        if name == "list_files":
            return list_files(root, str(args.get("directory", ".")), ref=head_ref)
        if name == "git_show":
            return git_show(root, str(args["path"]), base_ref)
        return f"ERROR: unknown tool '{name}'"
    except KeyError as exc:
        return f"ERROR: required parameter missing: {exc}"
    except (ToolError, subprocess.SubprocessError, OSError) as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001
        # The arguments are model output, so the shapes they arrive in are not
        # ours to enumerate. A tool call that blows up in an unforeseen way is
        # one wasted turn for one agent; letting it out of here takes down the
        # whole review, which is how a run of eight agents died on a `start_line`
        # that arrived as a string.
        return f"ERROR: the call failed ({type(exc).__name__}: {exc})"


def parse_arguments(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
