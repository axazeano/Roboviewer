"""Prompts from files: loading, per-file overriding, template errors."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from roboviewer.checklist import ChecklistItem
from roboviewer.pipeline import ReviewPipeline
from roboviewer.prompts import DEFAULT_DIR, NAMES, PromptError, Prompts

from .conftest import ScriptedRunner, make_bundle, ok_outcome


def item(**kw) -> ChecklistItem:
    base = {"id": "correctness", "title": "Корректность", "body": "Ищи ошибки."}
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
    (tmp_path / "item_system.md").write_text("Свой системный промпт.", encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    assert prompts.item_system == "Свой системный промпт."
    assert prompts.sources["item_system"] == str(tmp_path / "item_system.md")
    # The other files came from the bundle
    assert prompts.judge_system == Prompts.load().judge_system
    assert prompts.sources["judge_system"] == str(DEFAULT_DIR / "judge_system.md")


# ----------------------------------------------------------------- rendering


def test_item_prompt_contains_diff_and_checklist_body(tmp_path: Path) -> None:
    prompts = Prompts.load()
    bundle = make_bundle(tmp_path)
    text = prompts.build_item_prompt(item(), bundle)
    assert "Ищи ошибки." in text
    assert bundle.annotated in text
    assert "feature/x" in text


def test_context_scaffolding_comes_from_code_not_from_files(tmp_path: Path) -> None:
    # The context, the legend and the tail of non-inlined files are assembled by
    # the code, so a context.md in the user directory changes nothing
    (tmp_path / "context.md").write_text("Подменённый контекст", encoding="utf-8")
    text = Prompts.load(tmp_path).build_item_prompt(item(), make_bundle(tmp_path))
    assert "Подменённый контекст" not in text
    assert "Контекст merge request'а" in text
    assert "строка добавлена или изменена" in text, "the markup legend must reach the model"


def test_broken_placeholder_fails_loudly_and_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "item_user.md").write_text("Привет {нет_такого}", encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    with pytest.raises(PromptError) as err:
        prompts.build_item_prompt(item(), make_bundle(tmp_path))
    assert "item_user.md" in str(err.value)
    assert "doubled" in str(err.value), "the error must explain how to escape braces"


def test_validate_checks_every_template_before_spending_tokens(tmp_path: Path) -> None:
    (tmp_path / "judge_user.md").write_text("{опечатка}", encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    with pytest.raises(PromptError):
        prompts.validate([item()], make_bundle(tmp_path))


def test_literal_braces_survive_when_doubled(tmp_path: Path) -> None:
    (tmp_path / "item_system.md").write_text('Пример JSON: {{"a": 1}}', encoding="utf-8")
    prompts = Prompts.load(tmp_path)
    # System prompts are not run through format() and go out as-is
    assert prompts.item_system == 'Пример JSON: {{"a": 1}}'


# ------------------------------------------------------- wiring into the run


def test_agent_receives_prompts_from_the_custom_set(tmp_path: Path, config) -> None:
    custom = tmp_path / "prompts"
    custom.mkdir()
    (custom / "item_system.md").write_text("Свой системный промпт.", encoding="utf-8")

    runner = ScriptedRunner(ok_outcome())
    pipeline = ReviewPipeline(
        config, make_bundle(tmp_path), [item()], runner, prompts=Prompts.load(custom)
    )
    asyncio.run(pipeline.execute())

    assert runner.requests[0].system == "Свой системный промпт."


def test_checklist_system_override_still_beats_the_prompt_file(tmp_path: Path, config) -> None:
    runner = ScriptedRunner(ok_outcome())
    checklist = [item(system="Замена из _system.md")]
    pipeline = ReviewPipeline(config, make_bundle(tmp_path), checklist, runner)
    asyncio.run(pipeline.execute())

    assert runner.requests[0].system == "Замена из _system.md"
