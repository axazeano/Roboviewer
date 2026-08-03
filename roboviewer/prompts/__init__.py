"""Prompt templates, loaded from files.

Four texts get rewritten while tuning a model — the reviewer's system prompt and
task, the judge's — so they live in markdown rather than in string literals.

The scaffolding below stays in code. The context block, the tail listing files
that did not fit and the annotation legend are assembly over `DiffBundle`
fields, and the legend has to match the markup `gitdiff.py` actually emits.

The output-language directive is assembly too, over a config value. Keeping it
out of the templates means a custom prompt set gets the option for free instead
of having to carry a placeholder for it.

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
# Merge request context

Repository: {repo}
Branch: {branch} → {target}
Merge base: {base_sha}

## Changed files
```
{files}
```

## Changed files in full

{legend}

{annotated}
{fallback_block}"""

FALLBACK_BLOCK = """
## Files not attached in full

Too large, or deleted — only the changed fragments are below. Read the full
contents with `read_file`, and the state before the changes with `git_show`.

```diff
{diff}
```
"""

# Appended to the system prompt when a language is configured.
LANGUAGE_DIRECTIVE = """

# Output language

Write every text field you submit in {language}: titles, rationales,
suggestions, reasons and the summary.

Code, identifiers, file paths and quoted snippets stay exactly as they appear in
the source — do not translate those.
"""

# The same instruction again, last thing in the task. A small model drifts back
# to the language of the code it has been reading, and the final line is what
# survives that drift.
LANGUAGE_REMINDER = "\n\nWrite the text you submit in {language}."

# ISO-639-1 codes people actually type on a command line. Anything missing here
# goes into the prompt as written, so "Bahasa Indonesia" works just as well.
_LANGUAGE_NAMES = {
    "ar": "Arabic", "de": "German", "en": "English", "es": "Spanish",
    "fr": "French", "hi": "Hindi", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
    "ru": "Russian", "tr": "Turkish", "uk": "Ukrainian", "zh": "Chinese",
}


class PromptError(RuntimeError):
    pass


def language_name(value: str) -> str:
    """`ru` → `Russian`. Anything unknown passes through as written."""
    return _LANGUAGE_NAMES.get(value.strip().lower(), value.strip())


@dataclass
class Prompts:
    texts: dict[str, str]
    # name → path it was read from; --show-config prints this, so "which text
    # produced these findings" is never a matter of memory
    sources: dict[str, str]
    # Language the model writes its own prose in. Empty asks for nothing, so the
    # model answers in the language of the prompts.
    language: str = ""

    def __post_init__(self) -> None:
        self.language = language_name(self.language) if self.language else ""

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, directory: Path | None = None, language: str = "") -> "Prompts":
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
        return cls(texts=texts, sources=sources, language=language)

    def validate(self, items: list[ChecklistItem], diff: DiffBundle) -> None:
        """Renders every template against the real diff before any tokens are
        spent, so a broken placeholder fails at startup, not eight agents deep."""
        for item in items:
            self.build_item_prompt(item, diff)
        self.build_judge_prompt([], diff)

    # ---------------------------------------------------------------- rendering

    @property
    def item_system(self) -> str:
        return self._with_language(self.texts["item_system"])

    @property
    def judge_system(self) -> str:
        return self._with_language(self.texts["judge_system"])

    def system_for(self, item: ChecklistItem) -> str:
        """The reviewer's system prompt for one item. A checklist set may replace
        it with its own `_system.md`; the language directive applies either way."""
        return self._with_language(item.system or self.texts["item_system"])

    def _with_language(self, text: str) -> str:
        if not self.language:
            return text
        return text + LANGUAGE_DIRECTIVE.format(language=self.language)

    def _with_reminder(self, text: str) -> str:
        if not self.language:
            return text
        return text + LANGUAGE_REMINDER.format(language=self.language)

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
        return self._with_reminder(
            self._fmt(
                "item_user",
                context=_context_block(diff),
                item_title=item.title,
                item_body=item.body,
            )
        )

    def build_judge_prompt(self, findings: list[Finding], diff: DiffBundle) -> str:
        return self._with_reminder(
            self._fmt(
                "judge_user",
                context=_context_block(diff),
                count=len(findings),
                findings="\n\n".join(_render_finding(f) for f in findings),
            )
        )


def _context_block(diff: DiffBundle) -> str:
    fallback = ""
    if diff.fallback and diff.text:
        fallback = FALLBACK_BLOCK.format(diff=diff.text)
        if diff.truncated:
            fallback += "\n> Fragments were truncated by size; read the rest with the tools.\n"

    return CONTEXT_BLOCK.format(
        repo=diff.root.name,
        branch=diff.branch,
        target=diff.target,
        base_sha=diff.base_sha[:12],
        files=diff.summary_table(),
        legend=ANNOTATION_LEGEND,
        annotated=diff.annotated or "(no file was attached in full)",
        fallback_block=fallback,
    )


def _render_finding(finding: Finding) -> str:
    """A finding as the judge sees it — structure, not prose, so it stays in
    code rather than in a template."""
    lines = [
        f"## {finding.id} — {finding.title}",
        f"- File: `{finding.location}`",
        f"- Severity: {finding.severity.value}",
        f"- Category: {finding.category}",
        f"- Reviewer confidence: {finding.confidence:.2f}",
        f"- Found by: {', '.join(finding.sources) or '—'}",
        f"- Rationale: {finding.rationale}",
    ]
    if finding.suggestion:
        lines.append(f"- Suggestion: {finding.suggestion}")
    return "\n".join(lines)
