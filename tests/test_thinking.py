"""Reasoning mode: what goes into the request body and who receives it.

The setting is binary and noticeably changes the review, so "unset" means
silence: the model stays on its own default rather than someone else's choice.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from roboviewer.checklist import ChecklistItem
from roboviewer.config import ModelConfig
from roboviewer.models import Finding, Severity, Usage
from roboviewer.pipeline import ReviewPipeline
from roboviewer.runners import AgentOutcome

from .conftest import ScriptedRunner, make_bundle, ok_outcome

ITEM = ChecklistItem(id="correctness", title="Correctness", body="Find logic errors.")


# ------------------------------------------------------------- request body


def test_unset_sends_nothing() -> None:
    assert ModelConfig().request_body() == {}


def test_off_goes_out_as_chat_template_kwargs() -> None:
    body = ModelConfig(enable_thinking=False).request_body()
    assert body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_extra_body_is_merged_and_the_knob_wins() -> None:
    role = ModelConfig(
        enable_thinking=False,
        extra_body={"top_k": 20, "chat_template_kwargs": {"foo": 1, "enable_thinking": True}},
    )
    body = role.request_body()
    assert body["top_k"] == 20
    assert body["chat_template_kwargs"] == {"foo": 1, "enable_thinking": False}
    # The config must not be mutated: the body is rebuilt for every request
    assert role.extra_body["chat_template_kwargs"]["enable_thinking"] is True


def test_judge_follows_the_reviewer_until_it_has_a_section_of_its_own(config) -> None:
    config.reviewer.enable_thinking = False

    assert config.judge is None
    assert config.for_judge().enable_thinking is False

    config.judge = ModelConfig(enable_thinking=True)
    assert config.for_judge().enable_thinking is True


# ------------------------------------------------------------ wiring into a run


def test_each_stage_gets_its_own_mode(tmp_path: Path, config) -> None:
    config.run.enable_judge = True
    config.reviewer.enable_thinking = False
    config.judge = ModelConfig(enable_thinking=True)

    finding = Finding(file="src/cart.py", line=42, severity=Severity.MAJOR,
                      category="logic", title="Bug", rationale="Because", confidence=0.9)
    runner = ScriptedRunner(
        ok_outcome(findings=[finding.model_dump(mode="json")]),
        AgentOutcome(payload={"summary": "", "verdicts": []}, usage=Usage(), turns=1),
    )
    asyncio.run(ReviewPipeline(config, make_bundle(tmp_path), [ITEM], runner).execute())

    item_request, judge_request = runner.requests
    assert item_request.settings.enable_thinking is False
    assert judge_request.settings.enable_thinking is True


def test_default_run_leaves_the_model_alone(tmp_path: Path, config) -> None:
    runner = ScriptedRunner(ok_outcome())
    asyncio.run(ReviewPipeline(config, make_bundle(tmp_path), [ITEM], runner).execute())
    assert runner.requests[0].settings.enable_thinking is None
