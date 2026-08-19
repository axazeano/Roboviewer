"""Watching a run, and writing down what it did.

`Recorder` is the observer the tool hands its account to — `roboviewer.observe`
defines that seam and nothing else; everything about the log is here. One agent
gets one `AgentRecorder`, so an agent can only ever write its own part and the
file stays in the order things actually happened.

Written while the run happens rather than assembled at the end: a run that dies
halfway is the one worth reading, and its log is already on disk.
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any, TextIO

from roboviewer.models import ReviewRun, Usage
from roboviewer.observe import AgentKind, AgentObserver

from .records import (
    LOG,
    AgentRecord,
    AgentStatus,
    BlobRecord,
    CallRecord,
    FileRecord,
    ItemRecord,
    OutcomeRecord,
    RunRecord,
    TurnRecord,
    dumps,
    submitted,
)

# git grep says this when nothing matched, and the log should say 0 hits rather
# than "one line came back".
_NO_MATCHES = "No matches for:"
_MORE_MATCHES = re.compile(r"^\[\.\.\. (\d+) more matches \.\.\.\]$", re.MULTILINE)
_EMPTY_LISTING = "(empty)"
# How the tool hands an agent a failed call. It is text to the model, so the log
# has to recognise it the same way a reader would.
_ERROR = "ERROR:"


class AgentRecorder:
    """One agent's part of the log."""

    def __init__(
        self,
        recorder: Recorder,
        agent_id: str,
        kind: AgentKind,
        title: str,
        item_id: str = "",
    ) -> None:
        self._recorder = recorder
        self._id = agent_id
        self._kind = kind
        self._title = title
        self._item_id = item_id

    def started(self, *, system: str, prompt: str, max_turns: int) -> None:
        self._recorder.write(
            AgentRecord(
                a=self._id,
                kind=self._kind,
                title=self._title,
                item_id=self._item_id,
                system=self._recorder.blob(system),
                prompt=self._recorder.blob(prompt),
                max_turns=max_turns,
            )
        )

    def replied(self, turn: int, text: str | None, usage: Usage) -> None:
        self._recorder.write(
            TurnRecord(a=self._id, n=turn, text=(text or "").strip(), usage=usage)
        )

    def called(
        self, turn: int, tool: str, args: dict[str, Any], output: str, seconds: float = 0.0
    ) -> None:
        """A tool call, by what it asked for and the size of what came back.

        The answer itself is measured and dropped here, at the only point that
        ever sees it: it is repository content, and a log that kept it would
        grow with what the agents read rather than with what they did.
        """
        lines, hits = _shape(tool, output)
        self._recorder.write(
            CallRecord(
                a=self._id,
                n=turn,
                tool=tool,
                args=args,
                chars=len(output),
                lines=lines,
                hits=hits,
                error=output.startswith(_ERROR),
                seconds=round(seconds, 3),
            )
        )

    def finished(
        self,
        *,
        payload: dict[str, Any] | None,
        usage: Usage,
        turns: int,
        duration_s: float,
        error: str | None = None,
        truncated: bool = False,
    ) -> None:
        """How it ended. Read off the runner's outcome rather than off the
        finished run, so an interrupted run's log still says what happened to
        the agents that did finish."""
        status: AgentStatus = (
            "failed" if (error or payload is None) else "truncated" if truncated else "ok"
        )
        summary, findings, verdicts = submitted(payload)
        self._recorder.write(
            OutcomeRecord(
                a=self._id,
                status=status,
                turns=turns,
                duration_s=round(duration_s, 2),
                usage=usage,
                error=error,
                summary=summary,
                findings=findings,
                verdicts=verdicts,
            )
        )


class Recorder:
    """The run's log file, from the first line to the last.

    Nothing is known about the run until it opens: the log lives in the run's
    own directory, and the run id that names it is minted when the run starts.
    """

    def __init__(self) -> None:
        self._stream: TextIO | None = None
        self._seen: set[str] = set()
        self._agents = 0
        self.directory: Path | None = None
        # Agents run concurrently, and a call is recorded from whichever task
        # finished it. One line per write, under one lock.
        self._lock = threading.Lock()

    def opened(self, run: ReviewRun, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self._stream = (directory / LOG).open("w", encoding="utf-8")
        self.write(
            RunRecord(
                run_id=run.run_id,
                branch=run.branch,
                target=run.target,
                base_sha=run.base_sha,
                head_sha=run.head_sha,
                model=run.model,
                started_at=run.started_at,
                files=[FileRecord(**f.model_dump()) for f in run.files],
                items=[ItemRecord(id=i.item_id, title=i.item_title) for i in run.items],
            )
        )

    def agent(self, kind: AgentKind, title: str, item_id: str = "") -> AgentObserver:
        """A handle for one agent. Ids are assigned in the order agents start,
        which is also the order their first lines appear in the log."""
        with self._lock:
            self._agents += 1
            number = self._agents
        return AgentRecorder(self, f"a{number}", kind, title, item_id)

    def write(self, record: Any) -> None:
        """One record, on disk before this returns."""
        if self._stream is None:
            return
        line = dumps(record)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()

    def blob(self, text: str) -> str:
        """Stores a prompt once and returns the hash the records refer to it by."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        with self._lock:
            new = digest not in self._seen
            self._seen.add(digest)
        if new:
            self.write(BlobRecord(h=digest, text=text))
        return digest

    def closed(self) -> None:
        if self._stream is None:
            return
        with self._lock:
            self._stream.close()
            self._stream = None


def _shape(tool: str, output: str) -> tuple[int | None, int | None]:
    """(lines or entries, matches) — the size of an answer, never the answer.

    Read off the text the tools return, because that is what the agent got. The
    shapes are `roboviewer.tools`'s, and the tests that cover this run the real
    tools rather than a copy of their formatting.
    """
    if output.startswith(_ERROR):
        return None, None
    if tool == "grep":
        if output.startswith(_NO_MATCHES):
            return None, 0
        shown = len([ln for ln in output.splitlines() if ln.strip()])
        capped = _MORE_MATCHES.search(output)
        if capped:
            return None, shown - 1 + int(capped.group(1))
        return None, shown
    if tool == "list_files":
        return (0 if output.strip() == _EMPTY_LISTING else len(output.splitlines())), None
    # read_file and git_show: a header line, then the numbered content
    return max(0, len(output.splitlines()) - 1), None
