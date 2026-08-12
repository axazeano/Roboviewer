"""Prompt templates, loaded from files.

Eight texts get rewritten while tuning a model — a system prompt and a task for
the reviewer, and a pair each for the judge's batch pass, its per-finding
verification and its final calibration — so they live in markdown rather than
in string literals. `NAMES` below is the list that counts.

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
from ..resolve import ReferenceReport

DEFAULT_DIR = Path(__file__).resolve().parent / "default"

# Loader contract with the template files; adding a template means extending this
NAMES = (
    "item_system",
    "item_user",
    "judge_system",
    "judge_user",
    "judge_one_system",
    "judge_one_user",
    "judge_final_system",
    "judge_final_user",
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
{fallback_block}{references_block}"""

# Output of the resolution pre-pass. Two sections, worded differently on
# purpose: the resource misses are search results, the symbol list is a lead the
# agent has to finish checking. Saying so is what keeps it from being quoted as
# proof — an unverified claim costs the author more than a missing one.
REFERENCES_BLOCK = """
# Reference check

Run over the whole tree before this review, including files not attached above
(storyboards, build manifests, strings). Findings here are worth reporting, but
report them in your own words and only after you have looked at the code.
{sections}"""

RESOURCE_SECTION = """
## References that resolve to nothing ({count})

Searched and absent. These are search results, not guesses.

{rows}"""

SYMBOL_SECTION = """
## Identifiers with no definition in this repository ({count})

Introduced by this diff, used in a position that has to resolve, and found
nowhere outside the files the diff touches, with no declaration anywhere.

This list cannot see your dependencies, so symbols from frameworks and the SDK
legitimately appear in it and are NOT problems. Decide which is which; when a
name looks like it should belong to this repository, `grep` it yourself before
reporting anything.

{rows}"""

FALLBACK_BLOCK = """
## Files not attached in full

Too large, or deleted — only the changed fragments are below. Read the full
contents with `read_file`, and the state before the changes with `git_show`.

```diff
{diff}
```
"""

# Handed to a per-finding judge so it can still recognise a duplicate without
# seeing the other findings in full. Structure, not wording — see the docstring.
ROSTER_BLOCK = """\
# The other findings in this review

Listed so you can recognise a duplicate. Judge only the finding above; a
`duplicate` verdict is for the same problem already reported under a LOWER id.

{roster}"""

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
    def load(cls, directory: Path | None = None, language: str = "") -> Prompts:
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
                    f"Template {filename} not found in {directory} "
                    f"or in the bundled set ({DEFAULT_DIR})"
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
        self.build_judge_one_prompt(_PLACEHOLDER_FINDING, [], diff)
        self.build_judge_final_prompt([], {}, diff)

    # ---------------------------------------------------------------- rendering

    @property
    def item_system(self) -> str:
        return self._with_language(self.texts["item_system"])

    @property
    def judge_system(self) -> str:
        return self._with_language(self.texts["judge_system"])

    @property
    def judge_one_system(self) -> str:
        return self._with_language(self.texts["judge_one_system"])

    @property
    def judge_final_system(self) -> str:
        return self._with_language(self.texts["judge_final_system"])

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

    def build_judge_one_prompt(
        self, finding: Finding, others: list[Finding], diff: DiffBundle
    ) -> str:
        """The task for a judge that settles a single claim. `others` is every
        other finding in the run — a one-line roster, so `duplicate` survives the
        loss of the batch judge's view of the whole list."""
        return self._with_reminder(
            self._fmt(
                "judge_one_user",
                context=_context_block(diff),
                finding=_render_finding(finding),
                roster=_render_roster(others),
            )
        )

    def build_judge_final_prompt(
        self, findings: list[Finding], notes: dict[str, str], diff: DiffBundle
    ) -> str:
        """The task for the pass that rules on findings which already passed
        verification. `notes` is what each finding's own pass reported checking,
        keyed by finding id — carried over so this pass reads the check instead
        of repeating it."""
        return self._with_reminder(
            self._fmt(
                "judge_final_user",
                context=_context_block(diff),
                count=len(findings),
                findings="\n\n".join(
                    _render_finding(f, notes.get(f.id)) for f in findings
                ),
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
        references_block=_references_block(diff.references),
    )


def _references_block(report: ReferenceReport | None) -> str:
    """The pre-pass result. Absent when it did not run — an empty section would
    read as "nothing was found", which is a different claim."""
    if report is None or report.empty:
        return ""

    sections: list[str] = []
    if report.resource_misses:
        # Grouped by question and file: forty keys missing from one file is one
        # fact stated once, not forty repetitions of the same sentence.
        groups: dict[tuple[str, str], list[str]] = {}
        for _, question, value, path in report.resource_misses:
            groups.setdefault((question, path), []).append(value)
        rows = "\n".join(
            f"- {question}\n  in `{path}`: " + ", ".join(f"`{v}`" for v in values)
            for (question, path), values in groups.items()
        )
        sections.append(
            RESOURCE_SECTION.format(count=len(report.resource_misses), rows=rows)
        )

    if report.unresolved_symbols:
        rows = "\n".join(
            f"- `{name}` — referenced in {', '.join(f'`{p}`' for p in paths[:3])}"
            for name, paths in sorted(report.unresolved_symbols.items())
        )
        if report.symbols_truncated:
            # Never let a cap read as coverage
            rows += f"\n- [... {report.symbols_truncated} more, not listed ...]"
        sections.append(
            SYMBOL_SECTION.format(count=len(report.unresolved_symbols), rows=rows)
        )

    return REFERENCES_BLOCK.format(sections="\n".join(sections))


def _render_finding(finding: Finding, note: str | None = None) -> str:
    """A finding as the judge sees it — structure, not prose, so it stays in
    code rather than in a template. `note` is the verification a previous pass
    already did, shown only to a judge that comes after one.

    The reviewer's severity and confidence are deliberately absent. Both are
    guesses made before anything was verified, by an agent that saw one aspect
    of the diff and had only its own findings to rank against — and a judge
    shown them follows them. The same claim about `fastjson.c:71` was rejected
    when the reviewer hedged at 0.30 and confirmed as major when another
    reviewer wrote it up confidently; the code had not changed.
    """
    lines = [
        f"## {finding.id} — {finding.title}",
        f"- File: `{finding.location}`",
        f"- Category: {finding.category}",
        f"- Found by: {', '.join(finding.sources) or '—'}",
        f"- Rationale: {finding.rationale}",
    ]
    if finding.suggestion:
        lines.append(f"- Suggestion: {finding.suggestion}")
    if note:
        lines.append(f"- Verified: {note}")
    return "\n".join(lines)


def _render_roster(others: list[Finding]) -> str:
    """One line per other finding. Enough to spot a duplicate, cheap enough to
    repeat in every per-finding pass — and severity is not part of that, for
    the same reason it is missing from the finding itself."""
    if not others:
        return ""
    lines = [f"- {f.id} `{f.location}` — {f.title}" for f in sorted(others, key=lambda f: f.id)]
    return ROSTER_BLOCK.format(roster="\n".join(lines))


# Only ever rendered by `validate`, to prove the template resolves before any
# tokens are spent.
_PLACEHOLDER_FINDING = Finding(
    id="F000",
    file="path/to/file",
    line=1,
    title="placeholder",
    rationale="placeholder",
)
