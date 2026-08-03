"""Prompt templates, loaded from files.

Four texts get rewritten while tuning a model — the reviewer's system prompt and
task, the judge's — so they live in markdown rather than in string literals.
They stay in Russian: that is the language the reports come out in.

The scaffolding below stays in code. The context block, the tail listing files
that did not fit and the annotation legend are assembly over `DiffBundle`
fields, and the legend has to match the markup `gitdiff.py` actually emits.

Each template resolves on its own, falling back to the bundled default, so a
custom set carries only the files it changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..checklist import ChecklistItem
from ..gitdiff import ANNOTATION_LEGEND, DiffBundle
from ..models import Finding

DEFAULT_DIR = Path(__file__).resolve().parent / "default"

# Loader contract with the template files; adding a template means extending this
NAMES = (
    "item_system",
    "item_user",
    "judge_system",
    "judge_user",
)

# Structure rather than wording — see the module docstring.
CONTEXT_BLOCK = """\
# Контекст merge request'а

Репозиторий: {repo}
Ветка: {branch} → {target}
База сравнения (merge-base): {base_sha}

## Изменённые файлы
```
{files}
```

## Изменённые файлы целиком

{legend}

{annotated}
{fallback_block}"""

FALLBACK_BLOCK = """
## Файлы, не приложенные целиком

Слишком велики либо удалены — ниже только изменённые фрагменты. Полное
содержимое читай через read_file, состояние до изменений — через git_show.

```diff
{diff}
```
"""


class PromptError(RuntimeError):
    pass


@dataclass
class Prompts:
    texts: dict[str, str]
    # name → path it was read from; --show-config prints this, so "which text
    # produced these findings" is never a matter of memory
    sources: dict[str, str]

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, directory: Path | None = None) -> "Prompts":
        """Loads the set, falling back to the bundled default file by file."""
        texts: dict[str, str] = {}
        sources: dict[str, str] = {}
        for name in NAMES:
            filename = f"{name}.md"
            candidates = [directory / filename] if directory is not None else []
            candidates.append(DEFAULT_DIR / filename)
            path = next((c for c in candidates if c.is_file()), None)
            if path is None:
                raise PromptError(
                    f"Template {filename} not found in {directory} or in the bundled set ({DEFAULT_DIR})"
                )
            # Trailing newlines mean nothing to the model but break composition;
            # indentation inside the text is preserved.
            texts[name] = path.read_text(encoding="utf-8").strip("\n")
            sources[name] = str(path)
        return cls(texts=texts, sources=sources)

    def validate(self, items: list[ChecklistItem], diff: DiffBundle) -> None:
        """Renders every template against the real diff before any tokens are
        spent, so a broken placeholder fails at startup, not eight agents deep."""
        for item in items:
            self.build_item_prompt(item, diff)
        self.build_judge_prompt([], diff)

    # ---------------------------------------------------------------- rendering

    @property
    def item_system(self) -> str:
        return self.texts["item_system"]

    @property
    def judge_system(self) -> str:
        return self.texts["judge_system"]

    def _fmt(self, name: str, **values: object) -> str:
        try:
            return self.texts[name].format(**values)
        except (KeyError, IndexError, ValueError) as exc:
            raise PromptError(
                f"Template {self.sources[name]} failed to render ({exc!r}). "
                f"Placeholders are written as {{name}}; literal braces are doubled "
                f"as {{{{ and }}}}. The available placeholders are listed in "
                f"{DEFAULT_DIR / 'README.md'}"
            ) from exc

    def build_item_prompt(self, item: ChecklistItem, diff: DiffBundle) -> str:
        return self._fmt(
            "item_user",
            context=_context_block(diff),
            item_title=item.title,
            item_body=item.body,
        )

    def build_judge_prompt(self, findings: list[Finding], diff: DiffBundle) -> str:
        return self._fmt(
            "judge_user",
            context=_context_block(diff),
            count=len(findings),
            findings="\n\n".join(_render_finding(f) for f in findings),
        )


def _context_block(diff: DiffBundle) -> str:
    fallback = ""
    if diff.fallback and diff.text:
        fallback = FALLBACK_BLOCK.format(diff=diff.text)
        if diff.truncated:
            fallback += "\n> Фрагменты усечены по размеру, дочитывай тулами.\n"

    return CONTEXT_BLOCK.format(
        repo=diff.root.name,
        branch=diff.branch,
        target=diff.target,
        base_sha=diff.base_sha[:12],
        files=diff.summary_table(),
        legend=ANNOTATION_LEGEND,
        annotated=diff.annotated or "(ни один файл не приложен целиком)",
        fallback_block=fallback,
    )


def _render_finding(finding: Finding) -> str:
    """A finding as the judge sees it — structure, not prose, so it stays in
    code rather than in a template."""
    lines = [
        f"## {finding.id} — {finding.title}",
        f"- Файл: `{finding.location}`",
        f"- Важность: {finding.severity.value}",
        f"- Категория: {finding.category}",
        f"- Уверенность ревьюера: {finding.confidence:.2f}",
        f"- Нашли пункты: {', '.join(finding.sources) or '—'}",
        f"- Обоснование: {finding.rationale}",
    ]
    if finding.suggestion:
        lines.append(f"- Предложение: {finding.suggestion}")
    return "\n".join(lines)
