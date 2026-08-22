"""The log this package keeps of a run, and the page rendered from it.

What a run concluded is in the report. What it did on the way — which prompt
each agent was given, what it said between turns, which files it opened — is
here, and it is the only place the question "did this run read src/cart.py, or
walk past it" can be asked.

Three properties matter more than the fields. The tool itself must keep nothing:
a review nobody asked to watch writes no log and mentions none. The log must not
grow with what the tools returned, or the artifact becomes a second copy of the
repository. And it must be readable after a run that never finished, because
those are the runs worth reading.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import research
from research.cli import main as research_main
from roboviewer.checklist import ChecklistItem
from roboviewer.config import Config, ModelConfig, ProviderConfig, RunConfig
from roboviewer.models import DiffStat, ItemResult, ReviewRun, Usage
from roboviewer.observe import SILENT, RunObserver
from roboviewer.pipeline import ReviewPipeline
from roboviewer.prompts.tool_schemas import SUBMIT_FINDINGS_TOOL, tool_schemas
from roboviewer.provider import AgentRequest
from roboviewer.provider.openai_agent import OpenAIAgentRunner
from roboviewer.repo.tools import dispatch
from roboviewer.report import save

from .conftest import ScriptedRunner, make_bundle, ok_outcome

READ_OUTPUT = "src/cart.py (lines 1-3 of 412)\n     1\ta\n     2\tb\n     3\tc"


@pytest.fixture
def run() -> ReviewRun:
    return ReviewRun(
        run_id="20260820-120000",
        repo_root=".",
        branch="feature/discount",
        target="develop",
        base_sha="abc123",
        head_sha="def456",
        model="test-model",
        started_at="2026-08-20T12:00:00+00:00",
        files=[
            DiffStat(file="src/cart.py", status="M", added=4, removed=1),
            DiffStat(file="src/api.py", status="M", added=2, removed=0),
        ],
        items=[
            ItemResult(item_id="correctness", item_title="Correctness"),
            ItemResult(item_id="errors", item_title="Error handling"),
        ],
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository, so the sizes recorded are measured off what the tools
    actually return rather than off a copy of their formatting."""
    git = lambda *args: subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    git("git", "init", "-q")
    git("git", "config", "user.email", "t@example.com")
    git("git", "config", "user.name", "T")
    (tmp_path / "cart.py").write_text(
        "def apply(code):\n    total -= discount(code)\n" + "pass\n" * 30
    )
    git("git", "add", "-A")
    git("git", "commit", "-qm", "init")
    return tmp_path


def _recording(directory: Path, run: ReviewRun) -> research.Recorder:
    """A recorder watching a run whose directory is already known — what the
    pipeline does at the moment the run starts."""
    recorder = research.Recorder()
    recorder.opened(run, directory)
    return recorder


def viewed(directory: Path) -> research.TraceView:
    view = research.load(directory)
    assert view is not None
    return view


# ------------------------------------------------------------- what is recorded


def test_a_reasoning_model_that_says_nothing_out_loud_is_not_a_blank_turn(
    tmp_path: Path, run: ReviewRun
) -> None:
    """Qwen and DeepSeek answer with tool calls and an empty `content`, putting
    what they worked out in a field of their own. Without it the account of a
    thinking model is a list of greps with no reason attached to any of them."""
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.replied(1, "", Usage(), "The discount is applied before the total is recomputed.")
    recorder.closed()

    turn = viewed(tmp_path).items[0].turns[0]
    assert turn.text == ""
    assert turn.thinking.startswith("The discount")
    assert turn.thinking_preview.startswith("The discount")


def test_the_turn_the_agent_stopped_on_says_so(tmp_path: Path, run: ReviewRun) -> None:
    """The submission is not a tool call in the log — it is the outcome. The turn
    it happened on would otherwise read as a turn on which nothing happened."""
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.replied(1, "", Usage())
    agent.called(1, "grep", {"pattern": "x"}, "No matches for: x")
    agent.replied(2, "", Usage())
    agent.finished(payload={"summary": "done", "findings": []}, usage=Usage(), turns=2,
                   duration_s=1.0)
    recorder.closed()

    first, last = viewed(tmp_path).items[0].turns
    assert not first.ended
    assert last.ended


def test_a_read_is_recorded_by_its_file_and_line_range(tmp_path: Path, run: ReviewRun) -> None:
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="be careful", prompt="review this", max_turns=15)
    agent.replied(1, "Reading the cart.", Usage(prompt_tokens=100, completion_tokens=10))
    agent.called(
        1, "read_file", {"path": "src/cart.py", "start_line": 1, "end_line": 3}, READ_OUTPUT
    )
    recorder.closed()

    call = viewed(tmp_path).items[0].turns[0].calls[0]
    assert call.tool == "read_file"
    assert call.subject == "src/cart.py:1-3"
    assert call.lines == 3
    assert call.chars == len(READ_OUTPUT)


def test_a_search_is_recorded_by_its_hit_count(tmp_path: Path, repo: Path, run: ReviewRun) -> None:
    """Measured off what `git grep` actually returned, so a change to the tool's
    output cannot quietly turn hits into lines."""
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    for pattern in ("discount", "nothing-matches-this"):
        agent.called(
            1,
            "grep",
            {"pattern": pattern},
            dispatch(repo, "grep", {"pattern": pattern}, base_ref="HEAD", head_ref="HEAD",
                     max_read_lines=800),
        )
    recorder.closed()

    found, missed = viewed(tmp_path).items[0].turns[0].calls
    assert found.hits == 1
    assert missed.hits == 0


def test_a_failed_call_is_recorded_as_one(tmp_path: Path, repo: Path, run: ReviewRun) -> None:
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.called(
        1,
        "read_file",
        {"path": "gone.py"},
        dispatch(repo, "read_file", {"path": "gone.py"}, base_ref="HEAD", head_ref="HEAD",
                 max_read_lines=800),
    )
    recorder.closed()

    call = viewed(tmp_path).items[0].turns[0].calls[0]
    assert call.error
    assert call.lines is None


def test_the_verdict_says_the_turns_ran_out(tmp_path: Path, run: ReviewRun) -> None:
    """A submission forced by the turn limit reads exactly like a finished review
    unless the log says which one it was."""
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.finished(
        payload={"summary": "half done", "findings": []},
        usage=Usage(prompt_tokens=10, completion_tokens=1),
        turns=15,
        duration_s=1.0,
        truncated=True,
    )
    recorder.closed()

    assert viewed(tmp_path).items[0].status == "truncated"


# -------------------------------------------------------------------- the size


def test_the_log_grows_with_the_calls_not_with_their_answers(
    tmp_path: Path, run: ReviewRun
) -> None:
    """The constraint that decides the whole design: tool output is repository
    content, and a log that kept it would grow with what the agents read."""

    def log_size(directory: Path, calls: int, output: str) -> int:
        directory.mkdir()
        recorder = _recording(directory, run)
        agent = recorder.agent("item", "Correctness", "correctness")
        agent.started(system="s", prompt="p", max_turns=15)
        for turn in range(1, calls + 1):
            agent.called(turn, "read_file", {"path": "src/cart.py"}, output)
        recorder.closed()
        return (directory / research.LOG).stat().st_size

    small = log_size(tmp_path / "small", 5, "x" * 100)
    huge = log_size(tmp_path / "huge", 5, "x" * 200_000)
    more = log_size(tmp_path / "more", 10, "x" * 100)

    assert abs(huge - small) < 100, "the log followed the answers rather than the calls"
    assert more > small


def test_no_tool_output_reaches_the_log_or_the_page(tmp_path: Path, run: ReviewRun) -> None:
    secret = "SOMETHING_ONLY_THE_FILE_CONTAINS"
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.called(1, "read_file", {"path": "src/cart.py"}, f"src/cart.py\n     1\t{secret}")
    recorder.closed()
    page = research.render_into(tmp_path)

    assert secret not in (tmp_path / research.LOG).read_text(encoding="utf-8")
    assert page is not None
    assert secret not in page.read_text(encoding="utf-8")


def test_a_prompt_is_written_once_and_referred_to_afterwards(
    tmp_path: Path, run: ReviewRun
) -> None:
    """The judge asks one system prompt of every finding it verifies."""
    recorder = _recording(tmp_path, run)
    system = "JUDGE_SYSTEM " + "you are the judge. " * 500
    for finding in ("F001", "F002"):
        agent = recorder.agent("judge", f"judge {finding}", finding)
        agent.started(system=system, prompt=f"verify {finding}", max_turns=8)
    recorder.closed()

    text = (tmp_path / research.LOG).read_text(encoding="utf-8")
    assert text.count("JUDGE_SYSTEM") == 1

    first, second = viewed(tmp_path).judge
    assert first.system == system
    assert second.system == ""
    assert second.system_same_as == "judge F001"


# ------------------------------------------------------------- a run cut short


def test_a_run_that_never_finished_keeps_what_had_already_happened(
    tmp_path: Path, run: ReviewRun
) -> None:
    """Nothing is closed, nothing is flushed at the end: the process is gone."""
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.replied(1, "Started reading.", Usage(prompt_tokens=100, completion_tokens=10))
    agent.called(1, "read_file", {"path": "src/cart.py"}, READ_OUTPUT)

    view = viewed(tmp_path)
    assert view.stats.unfinished == 1
    assert view.items[0].status == "running"
    assert view.items[0].turns[0].calls[0].subject == "src/cart.py"
    assert research.render_into(tmp_path) is not None


def test_a_half_written_line_costs_its_own_line_and_nothing_else(tmp_path: Path) -> None:
    log = tmp_path / research.LOG
    log.write_text(
        '{"t":"run","run_id":"r","items":[{"id":"correctness","title":"Correctness"}]}\n'
        '{"t":"agent","a":"a1","kind":"item","title":"Correctness","item_id":"correctness"}\n'
        '{"t":"turn","a":"a1","n":1,"tex',
        encoding="utf-8",
    )
    assert viewed(tmp_path).items[0].title == "Correctness"


# ----------------------------------------------------------------- the reading


def test_the_judge_is_told_apart_from_the_reviewers(tmp_path: Path, run: ReviewRun) -> None:
    recorder = _recording(tmp_path, run)
    item = recorder.agent("item", "Correctness", "correctness")
    item.started(system="s", prompt="p", max_turns=1)
    recorder.agent("judge", "judge F001", "F001").started(system="j", prompt="v", max_turns=1)
    recorder.closed()

    view = viewed(tmp_path)
    assert [a.title for a in view.items] == ["Correctness"]
    assert [a.title for a in view.judge] == ["judge F001"]


def test_items_are_listed_in_checklist_order(tmp_path: Path, run: ReviewRun) -> None:
    """Agents run concurrently and start in whatever order the semaphore allows;
    the page lists aspects the way the checklist does."""
    recorder = _recording(tmp_path, run)
    for item_id, title in (("errors", "Error handling"), ("correctness", "Correctness")):
        recorder.agent("item", title, item_id).started(system="s", prompt="p", max_turns=1)
    recorder.closed()

    assert [a.item_id for a in viewed(tmp_path).items] == ["correctness", "errors"]


def test_the_page_says_which_changed_files_were_opened(tmp_path: Path, run: ReviewRun) -> None:
    recorder = _recording(tmp_path, run)
    agent = recorder.agent("item", "Correctness", "correctness")
    agent.started(system="s", prompt="p", max_turns=15)
    agent.called(1, "read_file", {"path": "src/cart.py"}, READ_OUTPUT)
    agent.called(1, "read_file", {"path": "docs/design.md"}, "docs/design.md\n     1\tx")
    recorder.closed()

    view = viewed(tmp_path)
    assert {f.file: f.readers for f in view.files} == {"src/cart.py": 1, "src/api.py": 0}
    assert view.elsewhere == ["docs/design.md"]

    page = research.render_into(tmp_path)
    assert page is not None
    assert "never opened" in page.read_text(encoding="utf-8")


# ------------------------------------------------------------------- the runner


def _completion(*calls: Any, content: str = "", reasoning: str = "") -> SimpleNamespace:
    message = SimpleNamespace(
        content=content, tool_calls=list(calls), reasoning_content=reasoning
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def _call(name: str, args: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"call-{name}", function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


def _drive(repo: Path, script: list[Any], handle: research.AgentRecorder) -> None:
    """The real tool-calling loop against scripted completions, patched at the
    request boundary — so what lands in the log is what the loop really did."""
    runner = OpenAIAgentRunner(ProviderConfig(api_key="k"), RunConfig(), repo, "HEAD", "HEAD")
    remaining = list(script)

    async def fake_send(kwargs: dict[str, Any], emit: object) -> Any:
        return remaining.pop(0)

    runner._send = fake_send  # type: ignore[method-assign]
    asyncio.run(
        runner.run(
            AgentRequest(
                system="be careful",
                prompt="review this",
                tools=tool_schemas("HEAD"),
                terminal_tool=SUBMIT_FINDINGS_TOOL,
                settings=ModelConfig(model="m", max_turns=3),
                observer=handle,
            )
        )
    )


def test_the_runner_picks_up_what_the_model_thought(
    tmp_path: Path, repo: Path, run: ReviewRun
) -> None:
    """Through the real loop, because the field is the provider's and the loop
    is the only place that ever sees a completion."""
    log = tmp_path / "log"
    log.mkdir()
    recorder = _recording(log, run)
    _drive(
        repo,
        [_completion(_call("submit_findings", {"summary": "", "findings": []}),
                     reasoning="Nothing here touches the cart.")],
        recorder.agent("item", "Correctness", "correctness"),
    )
    recorder.closed()

    assert viewed(log).items[0].turns[0].thinking == "Nothing here touches the cart."


def test_the_runner_records_the_turns_it_took(tmp_path: Path, repo: Path, run: ReviewRun) -> None:
    """Through the loop itself rather than through the recorder: what is worth
    asserting is that a real turn leaves a real line."""
    log = tmp_path / "log"
    log.mkdir()
    recorder = _recording(log, run)
    _drive(
        repo,
        [
            _completion(_call("read_file", {"path": "cart.py"}), content="Reading the cart."),
            _completion(_call("submit_findings", {"summary": "one race", "findings": [
                {"file": "cart.py", "line": 2, "severity": "blocker", "title": "unguarded total"}
            ]})),
        ],
        recorder.agent("item", "Correctness", "correctness"),
    )
    recorder.closed()

    agent = viewed(log).items[0]
    # The prompt as the agent received it, budget note and all
    assert "You have 3 turns" in agent.system
    assert agent.turns[0].text == "Reading the cart."
    assert agent.turns[-1].ended, "the turn it submitted on is marked as the last one"
    assert agent.turns[0].calls[0].subject == "cart.py"
    assert agent.turns[0].calls[0].lines == 32
    assert agent.status == "ok"
    assert [f.location for f in agent.findings] == ["cart.py:2"]
    assert agent.opened == ["cart.py"]


# ------------------------------------------------------------------ the wiring


def _execute(cfg: Config, root: Path, observer: RunObserver = SILENT) -> ReviewRun:
    pipeline = ReviewPipeline(
        cfg,
        make_bundle(root),
        [ChecklistItem(id="correctness", title="Correctness", body="Find logic errors.")],
        ScriptedRunner(ok_outcome(summary="nothing")),
    )
    return asyncio.run(pipeline.execute(observer))


def test_a_watched_run_writes_its_log_into_the_run_directory(
    config: Config, tmp_path: Path
) -> None:
    """The pipeline tells the observer which run it is and where its artifacts
    go, so nothing here has to read the config to find the directory."""
    recorder = research.Recorder()
    result = _execute(config, tmp_path, recorder)

    log = tmp_path / ".roboviewer" / "runs" / result.run_id / research.LOG
    assert log.exists()
    view = viewed(log.parent)
    assert view.meta.run_id == result.run_id
    assert [f.file for f in view.files] == ["src/cart.py"]


def test_a_run_nobody_watched_keeps_nothing(config: Config, tmp_path: Path) -> None:
    """The tool's own surface: a plain review writes a report and no account of
    itself. Nothing under the run directory comes from this package."""
    result = _execute(config, tmp_path)
    directory = tmp_path / ".roboviewer" / "runs" / result.run_id

    reports = save(result, directory, ["md"])

    assert reports == [directory / "report.md"]
    assert not (directory / research.LOG).exists()
    assert not (directory / research.PAGE).exists()


def test_the_page_command_renders_a_log_somebody_already_has(
    config: Config, tmp_path: Path
) -> None:
    recorder = research.Recorder()
    result = _execute(config, tmp_path, recorder)
    directory = tmp_path / ".roboviewer" / "runs" / result.run_id

    assert research_main(["page", str(directory)]) == 0
    assert (directory / research.PAGE).exists()


def test_the_page_command_says_so_when_there_is_no_log(tmp_path: Path) -> None:
    assert research_main(["page", str(tmp_path)]) == 2
