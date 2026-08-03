"""Read-only tools for the reviewer agent.

Deliberately nothing that writes to disk or runs arbitrary commands: the reviewer
must not edit the code it is reviewing. Every path is confined to the repository
root.

Everything is read from git by the reviewed branch's ref rather than from the
working copy. The review may target a branch that is not checked out, and one
that is may carry uncommitted edits that would shift line numbers away from what
the agent sees in the attached files.

The descriptions and the returned text below go to the model, so they are part
of the prompt surface and stay in Russian like the rest of it.
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
        raise ToolError(f"Путь вне репозитория запрещён: {rel}") from None
    return candidate


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + f"\n\n[... вывод усечён до {MAX_TOOL_OUTPUT} символов ...]"


# --------------------------------------------------------------------------- tools


def _relative(root: Path, path: str) -> str:
    return _safe_path(root, path).relative_to(root.resolve()).as_posix()


def read_file(root: Path, path: str, start_line: int | None = None, end_line: int | None = None,
              max_lines: int = 800, ref: str = "HEAD") -> str:
    target = _safe_path(root, path)
    if target.suffix.lower() in _TEXT_SUFFIX_BLOCKLIST:
        raise ToolError(f"Бинарный файл, чтение не поддерживается: {path}")

    rel = target.relative_to(root.resolve()).as_posix()
    content = show_file_at(root, ref, rel)
    if content is None:
        raise ToolError(f"Файл не найден в проверяемой ветке: {path}")
    if looks_binary(content):
        raise ToolError(f"Бинарный файл, чтение не поддерживается: {path}")

    lines = content.splitlines()
    start = max(1, start_line or 1)
    end = min(len(lines), end_line or start + max_lines - 1)
    if end - start + 1 > max_lines:
        end = start + max_lines - 1

    chunk = lines[start - 1 : end]
    numbered = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(chunk))
    header = f"{path} (строки {start}-{min(end, len(lines))} из {len(lines)})"
    return _truncate(f"{header}\n{numbered}")


def grep(root: Path, pattern: str, glob: str | None = None, ref: str = "HEAD",
         max_results: int = 60) -> str:
    # git grep, not ripgrep: we search the reviewed branch's tree, not the disk
    args = ["git", "grep", "-n", "-I", "-E", "--no-color", "-e", pattern, ref]
    if glob:
        args += ["--", glob]

    proc = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=60)
    if proc.returncode not in (0, 1):
        raise ToolError(f"git grep завершился с ошибкой: {proc.stderr.strip()[:500]}")

    prefix = f"{ref}:"
    lines = [
        ln[len(prefix):] if ln.startswith(prefix) else ln
        for ln in proc.stdout.splitlines()
        if ln.strip()
    ]
    if not lines:
        return f"Совпадений не найдено для: {pattern}"
    shown = lines[:max_results]
    suffix = f"\n[... ещё {len(lines) - len(shown)} совпадений ...]" if len(lines) > len(shown) else ""
    return _truncate("\n".join(shown) + suffix)


def list_files(root: Path, directory: str = ".", ref: str = "HEAD", max_entries: int = 200) -> str:
    rel = _relative(root, directory)
    spec = f"{ref}:{rel}" if rel not in ("", ".") else f"{ref}:"
    proc = subprocess.run(["git", "ls-tree", spec], cwd=root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ToolError(f"Каталог не найден в проверяемой ветке: {directory}")

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
    return "\n".join(sorted(entries)) or "(пусто)"


def git_show(root: Path, path: str, ref: str) -> str:
    rel = _relative(root, path)
    content = show_file_at(root, ref, rel)
    if content is None:
        raise ToolError(f"Не удалось получить {path} на ревизии {ref} (файл мог быть создан в этой ветке)")
    if looks_binary(content):
        raise ToolError(f"Бинарный файл, чтение не поддерживается: {path}")
    lines = content.splitlines()
    numbered = "\n".join(f"{i + 1:>6}\t{line}" for i, line in enumerate(lines[:800]))
    return _truncate(f"{path} @ {ref} ({len(lines)} строк)\n{numbered}")


# --------------------------------------------------------------------------- schemas


def tool_schemas(base_ref: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Прочитать файл в новой версии (на вершине проверяемой ветки) с нумерацией строк. "
                    "Нужен для файлов, не приложенных к заданию целиком, и для любого другого кода "
                    "репозитория: вызывающего, соседнего, тестов."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Путь относительно корня репозитория"},
                        "start_line": {"type": "integer", "description": "Первая строка (с 1)"},
                        "end_line": {"type": "integer", "description": "Последняя строка включительно"},
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
                    "Поиск по расширенному регулярному выражению во всём репозитории на "
                    "проверяемой ветке. Нужен, чтобы найти вызывающий код, тесты, дубликаты, "
                    "существующие паттерны."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Регулярное выражение"},
                        "glob": {"type": "string", "description": "Ограничение по маске файлов, например *.py или src/**"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "Список файлов и подкаталогов в каталоге репозитория на проверяемой ветке.",
                "parameters": {
                    "type": "object",
                    "properties": {"directory": {"type": "string", "description": "Путь относительно корня"}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_show",
                "description": (
                    f"Прочитать файл в состоянии ДО изменений (ревизия {base_ref[:12]}). "
                    "Используй, когда нужно сравнить старую и новую реализацию целиком."
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
            "Завершить проверку и вернуть найденные замечания. "
            "Вызывай ровно один раз в конце. Пустой список findings — нормальный результат."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "1-3 предложения: что проверено и общий вывод по этому пункту.",
                },
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "description": "Путь к файлу относительно корня репозитория"},
                            "line": {"type": "integer", "description": "Номер строки в НОВОЙ версии файла"},
                            "end_line": {"type": "integer"},
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "major", "minor", "nit"],
                            },
                            "category": {"type": "string", "description": "Короткий слаг, например null-safety, race, api-break"},
                            "title": {"type": "string", "description": "Суть проблемы одной строкой"},
                            "rationale": {"type": "string", "description": "Почему это проблема и при каком сценарии она проявится"},
                            "suggestion": {"type": "string", "description": "Конкретное предложение по исправлению"},
                            "confidence": {"type": "number", "description": "Уверенность от 0 до 1"},
                        },
                        "required": ["file", "severity", "category", "title", "rationale", "confidence"],
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
        "description": "Завершить работу судьи и вернуть вердикт по каждому замечанию.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Общий вывод по качеству MR, 2-5 предложений."},
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
                                "description": "Скорректированная важность, если исходная неверна",
                            },
                            "reason": {"type": "string", "description": "Короткое обоснование вердикта"},
                        },
                        "required": ["finding_id", "verdict", "reason"],
                    },
                },
            },
            "required": ["summary", "verdicts"],
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
        return f"ОШИБКА: неизвестный тул '{name}'"
    except KeyError as exc:
        return f"ОШИБКА: не хватает обязательного параметра {exc}"
    except (ToolError, subprocess.SubprocessError, OSError) as exc:
        return f"ОШИБКА: {exc}"


def parse_arguments(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
