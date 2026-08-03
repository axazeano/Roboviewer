"""An agent made to submit by the turn limit must not look like one that finished.

The last turn sets tool_choice to the terminal tool, so the agent always hands
something back. Reporting that as a clean pass turns "I ran out of turns" into
"I found nothing", which is the opposite conclusion.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from roboviewer.checklist import ChecklistItem
from roboviewer.config import ProviderConfig, RunConfig
from roboviewer.models import ItemResult, Usage
from roboviewer.pipeline import ReviewPipeline
from roboviewer.report import render_report
from roboviewer.runners import AgentOutcome
from roboviewer.runners.openai_agent import OpenAIAgentRunner
from roboviewer.tools import SUBMIT_FINDINGS_TOOL, tool_schemas
from roboviewer.view import build_view

from .conftest import ScriptedRunner, make_bundle
from .test_report import _run

ITEM = ChecklistItem(id="correctness", title="Correctness", body="Find logic errors.")


# ------------------------------------------------------------------- the runner


def _call(name: str, args: dict):
    return SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _completion(*calls, content: str = ""):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=list(calls)))],
        usage=None,
    )


def _runner(tmp_path: Path) -> OpenAIAgentRunner:
    return OpenAIAgentRunner(
        ProviderConfig(api_key="k"), RunConfig(), tmp_path, "base", "head"
    )


def _request(max_turns: int):
    from roboviewer.runners import AgentRequest

    return AgentRequest(
        system="s",
        prompt="p",
        tools=tool_schemas("base"),
        terminal_tool=SUBMIT_FINDINGS_TOOL,
        model="m",
        max_turns=max_turns,
    )


def _drive(tmp_path: Path, script: list, max_turns: int) -> AgentOutcome:
    """Runs the real tool-calling loop against a scripted sequence of completions."""
    runner = _runner(tmp_path)
    remaining = list(script)

    async def fake_complete(**_kw):
        return remaining.pop(0)

    runner._complete = fake_complete  # type: ignore[method-assign]
    return asyncio.run(runner.run(_request(max_turns)))


def test_submitting_early_is_not_truncated(tmp_path: Path) -> None:
    outcome = _drive(
        tmp_path,
        [_completion(_call("submit_findings", {"summary": "done", "findings": []}))],
        max_turns=5,
    )
    assert outcome.ok and not outcome.truncated
    assert outcome.turns == 1


def test_submitting_on_the_last_turn_is_truncated(tmp_path: Path) -> None:
    """tool_choice left the model no other move, so the submission is the limit
    talking rather than the agent deciding it was done."""
    script = [
        _completion(_call("list_files", {"directory": "."})),
        _completion(_call("submit_findings", {"summary": "", "findings": []})),
    ]
    outcome = _drive(tmp_path, script, max_turns=2)

    assert outcome.ok, "the payload is still real and must not be thrown away"
    assert outcome.truncated
    assert outcome.turns == 2


def test_the_plain_text_fallback_on_the_last_turn_is_truncated(tmp_path: Path) -> None:
    body = '```json\n{"summary": "", "findings": []}\n```'
    outcome = _drive(tmp_path, [_completion(content=body)], max_turns=1)

    assert outcome.ok and outcome.truncated


# ----------------------------------------------------------------- the pipeline


def _execute(tmp_path: Path, config, outcome: AgentOutcome):
    pipeline = ReviewPipeline(config, make_bundle(tmp_path), [ITEM], ScriptedRunner(outcome))
    return asyncio.run(pipeline.execute())


@pytest.mark.parametrize(
    ("truncated", "status"), [(False, "ok"), (True, "truncated")]
)
def test_pipeline_records_how_the_agent_stopped(
    tmp_path: Path, config, truncated: bool, status: str
) -> None:
    outcome = AgentOutcome(
        payload={"summary": "s", "findings": []}, usage=Usage(), turns=3, truncated=truncated
    )
    run = _execute(tmp_path, config, outcome)
    assert run.items[0].status == status


# --------------------------------------------------------------------- the view


def _truncated_run(summary: str = "as far as I got"):
    return _run(
        items=[
            ItemResult(item_id="correctness", item_title="Correctness", status="ok",
                       summary="checked", turns=4, usage=Usage(prompt_tokens=10)),
            ItemResult(item_id="concurrency", item_title="Concurrency", status="truncated",
                       summary=summary, turns=15, usage=Usage(prompt_tokens=10)),
        ]
    )


def test_view_separates_cut_off_items_from_clean_ones() -> None:
    view = build_view(_truncated_run())

    assert [i.id for i in view.truncated_items] == ["concurrency"]
    assert not view.failed_items, "running out of turns is not a failure"


def test_view_carries_the_summary_and_says_when_there_is_none() -> None:
    """An agent that submits without a conclusion is the usual shape of being cut
    off, so the empty summary has to survive as far as the template."""
    assert build_view(_truncated_run()).truncated_items[0].summary == "as far as I got"
    assert build_view(_truncated_run("   \n ")).truncated_items[0].summary is None


# ------------------------------------------------------------------ the report


def test_report_does_not_pass_a_cut_off_item_off_as_a_clean_one() -> None:
    text = render_report(_truncated_run())

    assert "| Concurrency | ⚠️ |" in text, "a cut-off item must not carry the ok tick"
    assert "| Correctness | ✅ |" in text
    assert "Cut off by the turn limit" in text
    assert "stopped at turn 15" in text
    assert "max_turns" in text, "the report should name the knob that fixes it"


def test_report_spells_out_a_missing_conclusion() -> None:
    assert "without a conclusion" in render_report(_truncated_run(""))


def test_a_clean_run_says_nothing_about_truncation() -> None:
    assert "Cut off by the turn limit" not in render_report(_run(items=[]))


def test_html_reports_it_too() -> None:
    html = render_report(_truncated_run(), "html")
    assert "Cut off by the turn limit" in html and "stopped at turn 15" in html


def test_sarif_warns_without_calling_the_run_a_failure() -> None:
    """Consumers gate on executionSuccessful; the review did run, so the signal
    belongs in a notification rather than in that flag."""
    invocation = json.loads(render_report(_truncated_run(), "sarif"))["runs"][0]["invocations"][0]

    assert invocation["executionSuccessful"] is True
    note = invocation["toolExecutionNotifications"][0]
    assert note["level"] == "warning"
    assert note["descriptor"]["id"] == "concurrency"
    assert "turn limit" in note["message"]["text"]
