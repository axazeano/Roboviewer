"""Prompt templates, loaded from files.

Four texts live here — the reviewer's system prompt and task, the judge's system
prompt and task. Those are the ones that get rewritten while tuning a model, and
editing a markdown file beats editing a string literal in code.

The scaffolding around them stays in code below: the MR context block, the tail
listing files that did not fit, and the annotation legend. Those are assembly
wired to `DiffBundle` fields rather than wording — the legend in particular has
to match the markup `gitdiff.py` actually emits, so it is not something to hand
out for editing.

Resolution is per file: each template is looked up in the user's directory first
and falls back to the bundled default, so a custom set carries only what it
actually changes and picks up improvements to the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..checklist import ChecklistItem
from ..gitdiff import ANNOTATION_LEGEND, DiffBundle
from ..models import SEVERITY_LABEL_RU, Finding

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
    # name → path the text came from; --show-config prints it, so that "which
    # text produced these findings" never becomes a matter of memory
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
                    f"Шаблон {filename} не найден ни в {directory}, ни в комплекте ({DEFAULT_DIR})"
                )
            # Trailing newlines are meaningless to the model but break template
            # composition; leading indentation inside the text is preserved.
            texts[name] = path.read_text(encoding="utf-8").strip("\n")
            sources[name] = str(path)
        return cls(texts=texts, sources=sources)

    def validate(self, items: list[ChecklistItem], diff: DiffBundle) -> None:
        """Renders every template against the real diff before any tokens are
        spent: a broken placeholder must fail the run at startup, not eight
        agents deep."""
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
                f"Шаблон {self.sources[name]} не отрендерился ({exc!r}). "
                f"Плейсхолдеры пишутся как {{имя}}, литеральные фигурные скобки "
                f"удваиваются: {{{{ и }}}}. Доступные плейсхолдеры перечислены в "
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
    """Serialization of a finding for the judge — structure, not prose, so it
    stays in code rather than in a template."""
    lines = [
        f"## {finding.id} — {finding.title}",
        f"- Файл: `{finding.location}`",
        f"- Важность: {SEVERITY_LABEL_RU[finding.severity]} ({finding.severity.value})",
        f"- Категория: {finding.category}",
        f"- Уверенность ревьюера: {finding.confidence:.2f}",
        f"- Нашли пункты: {', '.join(finding.sources) or '—'}",
        f"- Обоснование: {finding.rationale}",
    ]
    if finding.suggestion:
        lines.append(f"- Предложение: {finding.suggestion}")
    return "\n".join(lines)
