"""The eight prompt texts, loaded from files, and the prompts assembled from them.

Eight texts get rewritten while tuning a model — a system prompt and a task for
the reviewer, and a pair each for the judge's batch pass, its per-finding
verification and its final calibration — so they live in markdown rather than
in string literals. `NAMES` below is the list that counts.

Each template resolves on its own, falling back to the bundled default, so a
custom set carries only the files it changes. What the code adds around the
texts is in the modules beside this one: the context block (`context`), the
findings a judge is shown (`findings`), the output-language directive
(`language`), the tools (`tool_schemas`) and the turn notes (`turns`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...config import Config, overrides
from ...models import Finding
from ...repo import ChangeSet
from ..checklist import ChecklistItem
from .context import context_block
from .findings import render_finding, render_roster
from .language import language_name, with_directive, with_reminder

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


class PromptError(RuntimeError):
    pass


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

    @classmethod
    def for_run(cls, cfg: Config, root: Path) -> Prompts:
        """The set a run reads: the bundled texts, overridden file by file from
        the directory the config names or `.roboviewer/prompts/` in the repository."""
        directory = overrides.prompts_dir(cfg, root)
        # A configured directory that does not exist is a typo, not a request for
        # the defaults: the loader would fall back file by file and the run would
        # quietly go out with prompts nobody chose.
        if cfg.run.prompts_dir and (directory is None or not directory.is_dir()):
            raise PromptError(f"Prompts directory not found: {directory}")
        return cls.load(directory, cfg.run.output_language)

    @property
    def overridden(self) -> dict[str, str]:
        """Only the texts this set did not take from the bundled one: name → file."""
        return {
            name: source
            for name, source in self.sources.items()
            if Path(source).parent != DEFAULT_DIR
        }

    def validate(self, items: list[ChecklistItem], changes: ChangeSet) -> None:
        """Renders every template against the real diff before any tokens are
        spent, so a broken placeholder fails at startup, not eight agents deep."""
        for item in items:
            self.build_item_prompt(item, changes)
        self.build_judge_prompt([], changes)
        self.build_judge_one_prompt(_PLACEHOLDER_FINDING, [], changes)
        self.build_judge_final_prompt([], {}, changes)

    # ---------------------------------------------------------------- rendering

    @property
    def item_system(self) -> str:
        return with_directive(self.texts["item_system"], self.language)

    @property
    def judge_system(self) -> str:
        return with_directive(self.texts["judge_system"], self.language)

    @property
    def judge_one_system(self) -> str:
        return with_directive(self.texts["judge_one_system"], self.language)

    @property
    def judge_final_system(self) -> str:
        return with_directive(self.texts["judge_final_system"], self.language)

    def system_for(self, item: ChecklistItem) -> str:
        """The reviewer's system prompt for one item. A checklist set may replace
        it with its own `_system.md`; the language directive applies either way."""
        return with_directive(item.system or self.texts["item_system"], self.language)

    def build_item_prompt(self, item: ChecklistItem, changes: ChangeSet) -> str:
        return with_reminder(
            self._fmt(
                "item_user",
                context=context_block(changes),
                item_title=item.title,
                item_body=item.body,
            ),
            self.language,
        )

    def build_judge_prompt(self, findings: list[Finding], changes: ChangeSet) -> str:
        return with_reminder(
            self._fmt(
                "judge_user",
                context=context_block(changes),
                count=len(findings),
                findings="\n\n".join(render_finding(f) for f in findings),
            ),
            self.language,
        )

    def build_judge_one_prompt(
        self, finding: Finding, others: list[Finding], changes: ChangeSet
    ) -> str:
        """The task for a judge that settles a single claim. `others` is every
        other finding in the run — a one-line roster, so `duplicate` survives the
        loss of the batch judge's view of the whole list."""
        return with_reminder(
            self._fmt(
                "judge_one_user",
                context=context_block(changes),
                finding=render_finding(finding),
                roster=render_roster(others),
            ),
            self.language,
        )

    def build_judge_final_prompt(
        self, findings: list[Finding], notes: dict[str, str], changes: ChangeSet
    ) -> str:
        """The task for the pass that rules on findings which already passed
        verification. `notes` is what each finding's own pass reported checking,
        keyed by finding id — carried over so this pass reads the check instead
        of repeating it."""
        return with_reminder(
            self._fmt(
                "judge_final_user",
                context=context_block(changes),
                count=len(findings),
                findings="\n\n".join(
                    render_finding(f, notes.get(f.id)) for f in findings
                ),
            ),
            self.language,
        )

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


# Only ever rendered by `validate`, to prove the template resolves before any
# tokens are spent.
_PLACEHOLDER_FINDING = Finding(
    id="F000",
    file="path/to/file",
    line=1,
    title="placeholder",
    rationale="placeholder",
)
