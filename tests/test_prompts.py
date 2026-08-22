"""Prompts from files: loading, per-file overriding, template errors, output language."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from roboviewer.checklist import ChecklistItem
from roboviewer.models import Finding, Severity
from roboviewer.pipeline import ReviewPipeline
from roboviewer.prompts import DEFAULT_DIR, NAMES, PromptError, Prompts, language_name

from .conftest import ScriptedRunner, make_bundle, ok_outcome


def item(**kw) -> ChecklistItem:
    base = {"id": "correctness", "title": "Correctness", "body": "Find logic errors."}
    base.update(kw)
    return ChecklistItem(**base)


# ------------------------------------------------------------------- loading


def test_bundled_set_is_complete() -> None:
    prompts = Prompts.load()
    for name in NAMES:
        assert prompts.texts[name], name
        assert prompts.sources[name] == str(DEFAULT_DIR / f"{name}.md")


def test_missing_custom_dir_falls_back_to_bundled(tmp_path: Path) -> None:
    prompts = Prompts.load(tmp_path / "no-such-dir")
    assert prompts.texts == Prompts.load().texts


def test_partial_override_wins_only_for_its_file(tmp_path: Path) -> None:
    (tmp_path / "item_system.md").write_text("Custom system prompt.", encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    assert prompts.item_system == "Custom system prompt."
    assert prompts.sources["item_system"] == str(tmp_path / "item_system.md")
    # The other files came from the bundle
    assert prompts.judge_system == Prompts.load().judge_system
    assert prompts.sources["judge_system"] == str(DEFAULT_DIR / "judge_system.md")


# ----------------------------------------------------------------- rendering


def test_item_prompt_contains_diff_and_checklist_body(tmp_path: Path) -> None:
    prompts = Prompts.load()
    bundle = make_bundle(tmp_path)
    text = prompts.build_item_prompt(item(), bundle)
    assert "Find logic errors." in text
    assert bundle.attachments.annotated in text
    assert "feature/x" in text


def test_context_scaffolding_comes_from_code_not_from_files(tmp_path: Path) -> None:
    # The context, the legend and the tail of non-inlined files are assembled by
    # the code, so a context.md in the user directory changes nothing
    (tmp_path / "context.md").write_text("Replaced context", encoding="utf-8")
    text = Prompts.load(tmp_path).build_item_prompt(item(), make_bundle(tmp_path))
    assert "Replaced context" not in text
    assert "# Merge request context" in text
    assert "line added or changed in this MR" in text, "the markup legend must reach the model"


def test_the_judge_is_not_shown_what_the_reviewer_rated_it(tmp_path: Path) -> None:
    """Severity and confidence are guesses made before anything was checked, by
    an agent that ranked against its own findings only — and a judge shown them
    follows them. Every judging prompt has to be free of both."""
    prompts = Prompts.load()
    bundle = make_bundle(tmp_path)
    finding = Finding(
        id="F001", file="src/a.py", line=7, severity=Severity.BLOCKER, confidence=0.93,
        category="race", title="Token refresh is re-entrant",
        rationale="The guard is read outside the task that sets it.",
    )
    other = Finding(
        id="F002", file="src/a.py", line=90, severity=Severity.NIT, confidence=0.12,
        category="style", title="Stray debug print",
        rationale="Left over from the previous change.",
    )

    for text in (
        prompts.build_judge_prompt([finding, other], bundle),
        prompts.build_judge_one_prompt(finding, [other], bundle),
        prompts.build_judge_final_prompt([finding], {"F001": "grep showed no guard"}, bundle),
    ):
        assert "Token refresh is re-entrant" in text, "the claim itself must reach the judge"
        assert "blocker" not in text.lower()
        assert "0.93" not in text
        assert "confidence" not in text.lower()


def test_every_reviewer_prompt_forbids_checking_the_build() -> None:
    """Not a preference: a review that verifies compilability spends its turns
    on what the compiler reports anyway, and gets it wrong often enough to ship
    false blockers. Every system prompt a reviewer can run under says so."""
    bundled = Path("roboviewer/prompts/default/item_system.md")
    checklist_owned = Path("roboviewer/checklists").glob("*/_system.md")
    for path in [bundled, *checklist_owned]:
        text = path.read_text(encoding="utf-8")
        assert "Never check whether it builds" in text, path
        assert "stop at that thought" in text, path


def test_every_judge_prompt_rejects_a_build_claim_without_verifying_it() -> None:
    prompts = Prompts.load()
    for name in ("judge_system", "judge_one_system", "judge_final_system"):
        text = prompts.texts[name]
        assert "build settles" in text or "build settle" in text, name


def test_broken_placeholder_fails_loudly_and_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "item_user.md").write_text("Hello {no_such_thing}", encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    with pytest.raises(PromptError) as err:
        prompts.build_item_prompt(item(), make_bundle(tmp_path))
    assert "item_user.md" in str(err.value)
    assert "doubled" in str(err.value), "the error must explain how to escape braces"


def test_validate_checks_every_template_before_spending_tokens(tmp_path: Path) -> None:
    (tmp_path / "judge_user.md").write_text("{typo}", encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    with pytest.raises(PromptError):
        prompts.validate([item()], make_bundle(tmp_path))


def test_literal_braces_survive_when_doubled(tmp_path: Path) -> None:
    (tmp_path / "item_system.md").write_text('JSON example: {{"a": 1}}', encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    # System prompts are not run through format() and go out as-is
    assert prompts.item_system == 'JSON example: {{"a": 1}}'


# ----------------------------------------------------------- output language


def test_unset_language_asks_the_model_for_nothing() -> None:
    """The directive has to be absent, not empty: an "answer in " with no
    language would be worse than saying nothing at all."""
    plain = Prompts.load()
    assert plain.item_system == plain.texts["item_system"]
    assert plain.judge_system == plain.texts["judge_system"]
    assert "Output language" not in plain.build_item_prompt(item(), make_bundle(Path("/r")))


@pytest.mark.parametrize(
    ("given", "expected"),
    [("ru", "Russian"), ("RU", "Russian"), (" de ", "German"), ("Russian", "Russian")],
)
def test_iso_codes_become_names(given: str, expected: str) -> None:
    assert language_name(given) == expected


def test_an_unknown_language_is_passed_through_as_written() -> None:
    """The map only covers codes people type; the prompt takes anything a model
    might understand."""
    assert language_name("Bahasa Indonesia") == "Bahasa Indonesia"
    assert Prompts.load(language="Bahasa Indonesia").language == "Bahasa Indonesia"


def test_language_reaches_both_the_system_prompt_and_the_task(tmp_path: Path) -> None:
    prompts = Prompts.load(language="ru")

    assert "Write every text field you submit in Russian" in prompts.item_system
    assert "Write every text field you submit in Russian" in prompts.judge_system
    # Restated last in the task, where a small model is most likely to keep it
    assert prompts.build_item_prompt(item(), make_bundle(tmp_path)).endswith(
        "Write the text you submit in Russian."
    )
    assert prompts.build_judge_prompt([], make_bundle(tmp_path)).endswith(
        "Write the text you submit in Russian."
    )


def test_a_checklists_own_system_prompt_still_gets_the_language(tmp_path: Path) -> None:
    """A checklist set replacing item_system.md must not silently lose the
    setting — that override is how the grouped and single sets work."""
    prompts = Prompts.load(language="ru")
    replaced = prompts.system_for(item(system="Replacement from _system.md"))

    assert replaced.startswith("Replacement from _system.md")
    assert "Write every text field you submit in Russian" in replaced


# ------------------------------------------------------- wiring into the run


def test_agent_receives_prompts_from_the_custom_set(tmp_path: Path, config) -> None:
    custom = tmp_path / "prompts"
    custom.mkdir()
    (custom / "item_system.md").write_text("Custom system prompt.", encoding="utf-8")

    runner = ScriptedRunner(ok_outcome())
    pipeline = ReviewPipeline(
        config, make_bundle(tmp_path), [item()], runner, prompts=Prompts.load(custom)
    )
    asyncio.run(pipeline.execute())

    assert runner.requests[0].system == "Custom system prompt."


def test_checklist_system_override_still_beats_the_prompt_file(tmp_path: Path, config) -> None:
    runner = ScriptedRunner(ok_outcome())
    checklist = [item(system="Replacement from _system.md")]
    pipeline = ReviewPipeline(config, make_bundle(tmp_path), checklist, runner)
    asyncio.run(pipeline.execute())

    assert runner.requests[0].system == "Replacement from _system.md"


def test_the_language_a_run_was_given_reaches_the_agent(tmp_path: Path, config) -> None:
    runner = ScriptedRunner(ok_outcome())
    pipeline = ReviewPipeline(
        config, make_bundle(tmp_path), [item()], runner, prompts=Prompts.load(language="ru")
    )
    asyncio.run(pipeline.execute())

    assert "in Russian" in runner.requests[0].system
    assert runner.requests[0].prompt.endswith("Write the text you submit in Russian.")
