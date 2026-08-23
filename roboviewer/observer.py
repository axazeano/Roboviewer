"""The one protocol a run reports through, with nobody listening by default.

Everything a run has to say about itself — that it started, that an item
finished, what an agent asked its tools, how it ended — goes to one
`RunObserver`. The console is one implementation and prints a line per event;
the research recorder is another and writes a log; `Broadcast` lets both listen
at once. The pipeline and the runner know only this protocol, so an instrument
built on it stays off the review path: the tool keeps nothing, decides no
format and writes no file of its own.

Two protocols rather than one, because they have different lifetimes: the run
hands out one `AgentObserver` per agent and holds the `RunObserver` for as long
as the run lasts. `Observer` is the no-op implementation of both — subclass it
and override what you care about.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from .models import ItemResult, ReviewRun, Usage

# What kind of agent is reporting. The two the pipeline fans out into: one per
# checklist item, and the judge's passes over what they found.
AgentKind = Literal["item", "judge"]


class AgentObserver(Protocol):
    """One agent's account of itself, in the order things happened.

    Plain values rather than the runner's own types: a runner is free to be
    something other than an OpenAI loop, and an observer should not have to know
    which one it was.
    """

    def started(self, *, system: str, prompt: str, max_turns: int) -> None:
        """The prompts as the agent received them, assembly included."""
        ...

    def replied(
        self, turn: int, text: str | None, usage: Usage, thinking: str = ""
    ) -> None:
        """One reply. `text` is what the model said out loud and `thinking` what
        it reasoned in the field some models answer in beside it — both, because
        a reasoning model says nothing at all in `text` on most turns."""
        ...

    def called(
        self, turn: int, tool: str, args: dict[str, Any], output: str, seconds: float
    ) -> None:
        """A tool call and its whole answer. What is worth keeping out of that
        answer is the observer's decision, not the tool's."""
        ...

    def progress(self, kind: str, detail: str) -> None:
        """What the runner says about its own work between the model's turns:
        a tool about to run, a retry, a pause for pacing, the wrap-up warning,
        the submission. `kind` is one short word, `detail` the rest."""
        ...

    def finished(
        self,
        *,
        payload: dict[str, Any] | None,
        usage: Usage,
        turns: int,
        duration_s: float,
        error: str | None,
        truncated: bool,
    ) -> None: ...


class RunObserver(Protocol):
    """Whoever is watching the run the agents belong to.

    `run_started` names the run and the directory its artifacts go in, so an
    observer that writes something has somewhere to put it without knowing how
    the tool is configured. Everything between it and `run_finished` is the
    pipeline's progress, stage by stage.
    """

    def run_started(self, run: ReviewRun, directory: Path) -> None: ...

    def item_started(self, item_id: str, title: str) -> None: ...

    def item_finished(self, item_id: str, title: str, result: ItemResult) -> None: ...

    def merged(self, count: int) -> None:
        """How many findings are left after merging what the items reported."""
        ...

    def out_of_scope(self, count: int) -> None:
        """How many findings pointed outside the changed lines and were set aside."""
        ...

    def judging(self, message: str) -> None:
        """A judging pass announcing itself — the wording is the judge's, since
        which passes there are is the judge mode's business."""
        ...

    def judged(self, confirmed: int, total: int) -> None: ...

    def failed(self, message: str) -> None:
        """Something went wrong short of ending the run: an item that failed, a
        judging pass that did not come back."""
        ...

    def run_finished(self, run: ReviewRun, message: str) -> None: ...

    def agent(self, kind: AgentKind, title: str, item_id: str = "") -> AgentObserver: ...


class Observer:
    """Nobody is watching — and the base to build a watcher on.

    Every method is a no-op, and `agent` returns the same silence, so a runner
    reports what it did the same way whether or not anyone kept it: no branch,
    nothing to forget. An implementation overrides only what it cares about.
    """

    # ---------------------------------------------------------------- the run

    def run_started(self, run: ReviewRun, directory: Path) -> None: ...

    def item_started(self, item_id: str, title: str) -> None: ...

    def item_finished(self, item_id: str, title: str, result: ItemResult) -> None: ...

    def merged(self, count: int) -> None: ...

    def out_of_scope(self, count: int) -> None: ...

    def judging(self, message: str) -> None: ...

    def judged(self, confirmed: int, total: int) -> None: ...

    def failed(self, message: str) -> None: ...

    def run_finished(self, run: ReviewRun, message: str) -> None: ...

    def agent(
        self, kind: AgentKind, title: str, item_id: str = ""  # noqa: ARG002 — see the protocol
    ) -> AgentObserver:
        return self

    # -------------------------------------------------------------- one agent

    def started(self, *, system: str, prompt: str, max_turns: int) -> None: ...

    def replied(
        self, turn: int, text: str | None, usage: Usage, thinking: str = ""
    ) -> None: ...

    def called(
        self, turn: int, tool: str, args: dict[str, Any], output: str, seconds: float
    ) -> None: ...

    def progress(self, kind: str, detail: str) -> None: ...

    def finished(
        self,
        *,
        payload: dict[str, Any] | None,
        usage: Usage,
        turns: int,
        duration_s: float,
        error: str | None,
        truncated: bool,
    ) -> None: ...


class Broadcast(Observer):
    """Several observers hearing the same run — the console and a recorder, say."""

    def __init__(self, observers: Sequence[RunObserver]) -> None:
        self._observers = list(observers)

    def run_started(self, run: ReviewRun, directory: Path) -> None:
        for observer in self._observers:
            observer.run_started(run, directory)

    def item_started(self, item_id: str, title: str) -> None:
        for observer in self._observers:
            observer.item_started(item_id, title)

    def item_finished(self, item_id: str, title: str, result: ItemResult) -> None:
        for observer in self._observers:
            observer.item_finished(item_id, title, result)

    def merged(self, count: int) -> None:
        for observer in self._observers:
            observer.merged(count)

    def out_of_scope(self, count: int) -> None:
        for observer in self._observers:
            observer.out_of_scope(count)

    def judging(self, message: str) -> None:
        for observer in self._observers:
            observer.judging(message)

    def judged(self, confirmed: int, total: int) -> None:
        for observer in self._observers:
            observer.judged(confirmed, total)

    def failed(self, message: str) -> None:
        for observer in self._observers:
            observer.failed(message)

    def run_finished(self, run: ReviewRun, message: str) -> None:
        for observer in self._observers:
            observer.run_finished(run, message)

    def agent(self, kind: AgentKind, title: str, item_id: str = "") -> AgentObserver:
        return _BroadcastAgent([o.agent(kind, title, item_id) for o in self._observers])


class _BroadcastAgent(Observer):
    def __init__(self, agents: Sequence[AgentObserver]) -> None:
        self._agents = list(agents)

    def started(self, *, system: str, prompt: str, max_turns: int) -> None:
        for agent in self._agents:
            agent.started(system=system, prompt=prompt, max_turns=max_turns)

    def replied(
        self, turn: int, text: str | None, usage: Usage, thinking: str = ""
    ) -> None:
        for agent in self._agents:
            agent.replied(turn, text, usage, thinking)

    def called(
        self, turn: int, tool: str, args: dict[str, Any], output: str, seconds: float
    ) -> None:
        for agent in self._agents:
            agent.called(turn, tool, args, output, seconds)

    def progress(self, kind: str, detail: str) -> None:
        for agent in self._agents:
            agent.progress(kind, detail)

    def finished(
        self,
        *,
        payload: dict[str, Any] | None,
        usage: Usage,
        turns: int,
        duration_s: float,
        error: str | None,
        truncated: bool,
    ) -> None:
        for agent in self._agents:
            agent.finished(
                payload=payload, usage=usage, turns=turns, duration_s=duration_s,
                error=error, truncated=truncated,
            )


SILENT = Observer()
