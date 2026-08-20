"""Somewhere for an agent to say what it did, with nobody listening by default.

`events` is what a run reports about itself while it runs: enough for a console
line. This is the other end of the same idea — everything an agent did, in full,
for whoever cares to keep it. The prompt it was handed, what it said on each
turn, every tool call and what came back.

The tool keeps none of it. It decides no format, writes no file and renders no
page: it hands over what happened and moves on. That is what keeps an instrument
built on this off the review path — `research` is one such instrument, and
nothing about it is visible from here.

Two protocols rather than one, because they have different lifetimes: the run
hands out one `AgentObserver` per agent and holds the `RunObserver` for as long
as the run lasts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from .models import ReviewRun, Usage

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

    `opened` names the run and the directory its artifacts go in, so an observer
    that writes something has somewhere to put it without knowing how the tool
    is configured.
    """

    def opened(self, run: ReviewRun, directory: Path) -> None: ...

    def agent(self, kind: AgentKind, title: str, item_id: str = "") -> AgentObserver: ...

    def closed(self) -> None: ...


class Silence:
    """Nobody is watching.

    `agent` returns the same silence, so a runner reports what it did the same
    way whether or not anyone kept it — no branch, nothing to forget.
    """

    def opened(self, run: ReviewRun, directory: Path) -> None: ...

    def agent(
        self, kind: AgentKind, title: str, item_id: str = ""  # noqa: ARG002 — see the protocol
    ) -> AgentObserver:
        return self

    def closed(self) -> None: ...

    def started(self, *, system: str, prompt: str, max_turns: int) -> None: ...

    def replied(
        self, turn: int, text: str | None, usage: Usage, thinking: str = ""
    ) -> None: ...

    def called(
        self, turn: int, tool: str, args: dict[str, Any], output: str, seconds: float
    ) -> None: ...

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


SILENT = Silence()
