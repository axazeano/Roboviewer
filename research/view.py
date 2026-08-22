"""The log, read back as something a page can be rendered from.

The records are written for a machine appending to a file mid-run; this is the
same run answered as questions a person asks. Which files were opened. Where the
turns went. What the agent said before it went looking.

Counting happens here rather than in the template, for the same reason it does
in `roboviewer/view.py`: two templates must not be able to disagree about how
many files a run opened. Wording stays in the template.

A prompt is carried by the first agent that used it and referred to by name
afterwards. The judge asks one system prompt of every finding it verifies, and
a page that repeated it thirty times would be thirty times the file for nothing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from roboviewer.models import ItemStatus, repo_path
from roboviewer.observer import AgentKind

from .records import (
    LOG,
    AgentRecord,
    BlobRecord,
    CallRecord,
    OutcomeRecord,
    RunRecord,
    SubmittedFinding,
    SubmittedVerdict,
    TurnRecord,
    loads,
)

# Tools that open a file, as opposed to searching or listing. What "the run
# opened this file" means on the page.
OPENING = ("read_file", "git_show")

# How much of a reply the collapsed line shows.
PREVIEW_CHARS = 120


class CallView(BaseModel):
    """One tool call: what was asked for, and the size of the answer."""

    tool: str
    # The arguments as one line — a path with its line range, a pattern, a
    # directory. Composed here because it is the arguments, not prose.
    subject: str
    chars: int = 0
    lines: int | None = None
    hits: int | None = None
    error: bool = False
    seconds: float = 0.0


class TurnView(BaseModel):
    n: int
    text: str = ""
    # What the model reasoned before answering, where the provider hands it back
    thinking: str = ""
    # The first line of whichever of the two there is, for the collapsed row
    preview: str = ""
    thinking_preview: str = ""
    tokens: int = 0
    calls: list[CallView] = Field(default_factory=list)
    # The turn the agent stopped on: it submitted here, or died here. Without it
    # a turn whose only call was the submission reads as a turn that did nothing.
    ended: bool = False


class AgentView(BaseModel):
    id: str
    kind: AgentKind
    title: str
    item_id: str = ""
    # "running" is what an agent that never came back looks like from here: the
    # log ends mid-flight and no outcome was ever written.
    status: ItemStatus = "running"
    error: str | None = None
    summary: str = ""
    system: str = ""
    prompt: str = ""
    # Set instead of the text when another agent was given the same one
    system_same_as: str | None = None
    prompt_same_as: str | None = None
    system_chars: int = 0
    prompt_chars: int = 0
    turns: list[TurnView] = Field(default_factory=list)
    turn_count: int = 0
    max_turns: int = 0
    calls: int = 0
    reads: int = 0
    searches: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    duration_s: float = 0.0
    findings: list[SubmittedFinding] = Field(default_factory=list)
    verdicts: list[SubmittedVerdict] = Field(default_factory=list)
    # Changed or not, every file this agent opened, in the order it first did
    opened: list[str] = Field(default_factory=list)


class FileView(BaseModel):
    """A changed file and how many agents opened it. Zero is the answer the
    coverage question was asked for."""

    file: str
    status: str = "M"
    added: int = 0
    removed: int = 0
    readers: int = 0


class TraceMeta(BaseModel):
    run_id: str = ""
    branch: str = ""
    target: str = ""
    base_sha: str = ""
    head_sha: str = ""
    model: str = ""
    started_at: str = ""


class TraceStats(BaseModel):
    agents: int = 0
    turns: int = 0
    calls: int = 0
    reads: int = 0
    searches: int = 0
    total_tokens: int = 0
    files_changed: int = 0
    files_opened: int = 0
    # An agent that never came back leaves no outcome, which is what an
    # interrupted run looks like from here.
    unfinished: int = 0


class TraceView(BaseModel):
    meta: TraceMeta
    stats: TraceStats
    files: list[FileView] = Field(default_factory=list)
    # Opened during the run but not changed by it — where the agents went looking
    elsewhere: list[str] = Field(default_factory=list)
    items: list[AgentView] = Field(default_factory=list)
    judge: list[AgentView] = Field(default_factory=list)


def load(directory: Path) -> TraceView | None:
    """The log in a run directory, or None when the run kept none."""
    log = directory / LOG
    if not log.exists():
        return None
    return build(log.read_text(encoding="utf-8"))


def build(text: str) -> TraceView:
    """The view over a log's text. Separate from `load` so a test can hand it
    lines rather than a directory."""
    return _Reader(text).view()


class _Reader:
    """Records back into agents, in one pass over the log.

    A class rather than a function because the pass fills in half a dozen
    tables at once, and threading those through arguments read worse than
    holding them.
    """

    def __init__(self, text: str) -> None:
        self._run = RunRecord(run_id="")
        self._blobs: dict[str, str] = {}
        # Which agent showed a prompt first, so the rest can point at it
        self._owner: dict[str, str] = {}
        self._agents: dict[str, AgentView] = {}
        self._turns: dict[tuple[str, int], TurnView] = {}
        self._readers: dict[str, set[str]] = {}
        for line in text.splitlines():
            self._take(line)

    def view(self) -> TraceView:
        agents = list(self._agents.values())
        for agent in agents:
            agent.turns.sort(key=lambda t: t.n)
        files = [
            FileView(**record.model_dump(), readers=len(self._readers.get(record.file, ())))
            for record in self._run.files
        ]
        changed = {f.file for f in files}
        return TraceView(
            meta=TraceMeta(**self._run.model_dump(include=set(TraceMeta.model_fields))),
            stats=self._stats(agents, files),
            files=files,
            elsewhere=sorted(path for path in self._readers if path not in changed),
            items=self._ordered_items(agents),
            judge=[a for a in agents if a.kind == "judge"],
        )

    # ---------------------------------------------------------------- one line

    def _take(self, line: str) -> None:
        record = loads(line)
        if isinstance(record, RunRecord):
            self._run = record
        elif isinstance(record, BlobRecord):
            self._blobs[record.h] = record.text
        elif isinstance(record, AgentRecord):
            self._agents[record.a] = self._agent(record)
        elif isinstance(record, TurnRecord):
            turn = self._turn(record.a, record.n)
            turn.text = record.text
            turn.thinking = record.thinking
            turn.preview = _preview(record.text)
            turn.thinking_preview = _preview(record.thinking)
            turn.tokens = record.usage.total_tokens
            self._count_turn(record)
        elif isinstance(record, CallRecord):
            self._call(record)
        elif isinstance(record, OutcomeRecord):
            self._outcome(record)

    def _agent(self, record: AgentRecord) -> AgentView:
        system, system_same_as = self._prompt(record.system, record.title)
        prompt, prompt_same_as = self._prompt(record.prompt, record.title)
        return AgentView(
            id=record.a,
            kind=record.kind,
            title=record.title,
            item_id=record.item_id,
            max_turns=record.max_turns,
            system=system,
            prompt=prompt,
            system_same_as=system_same_as,
            prompt_same_as=prompt_same_as,
            system_chars=len(self._blobs.get(record.system, "")),
            prompt_chars=len(self._blobs.get(record.prompt, "")),
        )

    def _prompt(self, digest: str, title: str) -> tuple[str, str | None]:
        """(the text, or whose page section already carries it)."""
        if not digest:
            return "", None
        owner = self._owner.setdefault(digest, title)
        if owner != title:
            return "", owner
        return self._blobs.get(digest, ""), None

    def _turn(self, agent_id: str, number: int) -> TurnView:
        key = (agent_id, number)
        if key not in self._turns:
            turn = TurnView(n=number)
            self._turns[key] = turn
            if agent_id in self._agents:
                self._agents[agent_id].turns.append(turn)
        return self._turns[key]

    def _count_turn(self, record: TurnRecord) -> None:
        agent = self._agents.get(record.a)
        if agent is None:
            return
        agent.turn_count = max(agent.turn_count, record.n)
        agent.total_tokens += record.usage.total_tokens
        agent.cached_tokens += record.usage.cached_tokens

    def _call(self, record: CallRecord) -> None:
        self._turn(record.a, record.n).calls.append(
            CallView(
                tool=record.tool,
                subject=_subject(record),
                chars=record.chars,
                lines=record.lines,
                hits=record.hits,
                error=record.error,
                seconds=record.seconds,
            )
        )
        agent = self._agents.get(record.a)
        if agent is None:
            return
        agent.calls += 1
        if record.tool in OPENING:
            agent.reads += 1
            path = _path(record.args.get("path"))
            if path and not record.error:
                self._readers.setdefault(path, set()).add(record.a)
                if path not in agent.opened:
                    agent.opened.append(path)
        elif record.tool == "grep":
            agent.searches += 1

    def _outcome(self, record: OutcomeRecord) -> None:
        agent = self._agents.get(record.a)
        if agent is None:
            return
        agent.status = record.status
        agent.error = record.error
        agent.summary = record.summary
        agent.findings = record.findings
        agent.verdicts = record.verdicts
        agent.duration_s = record.duration_s
        agent.turn_count = max(agent.turn_count, record.turns)
        self._turn(record.a, record.turns).ended = True
        # The runner's own total wins over the turns added up: a turn whose
        # reply never arrived still cost the prompt it was sent with.
        if record.usage.total_tokens:
            agent.total_tokens = record.usage.total_tokens
            agent.cached_tokens = record.usage.cached_tokens

    # ------------------------------------------------------------------ totals

    def _stats(self, agents: list[AgentView], files: list[FileView]) -> TraceStats:
        return TraceStats(
            agents=len(agents),
            turns=sum(a.turn_count for a in agents),
            calls=sum(a.calls for a in agents),
            reads=sum(a.reads for a in agents),
            searches=sum(a.searches for a in agents),
            total_tokens=sum(a.total_tokens for a in agents),
            files_changed=len(files),
            files_opened=sum(1 for f in files if f.readers),
            unfinished=sum(1 for a in agents if a.status == "running"),
        )

    def _ordered_items(self, agents: list[AgentView]) -> list[AgentView]:
        """Checklist order, not the order the agents happened to start in — the
        page lists aspects the way the checklist does."""
        items = [a for a in agents if a.kind == "item"]
        order = {item.id: index for index, item in enumerate(self._run.items)}
        return sorted(items, key=lambda a: order.get(a.item_id, len(order)))


def _subject(record: CallRecord) -> str:
    """The call's arguments on one line, in the terms the tool takes them."""
    args = record.args
    if record.tool == "grep":
        pattern = str(args.get("pattern", ""))
        glob = args.get("glob")
        return f"{pattern} in {glob}" if glob else pattern
    if record.tool == "list_files":
        return str(args.get("directory", ".") or ".")
    path = _path(args.get("path")) or "?"
    start, end = args.get("start_line"), args.get("end_line")
    if start and end:
        return f"{path}:{start}-{end}"
    if start:
        return f"{path}:{start}-"
    return path


def _path(value: object) -> str:
    return repo_path(value) if isinstance(value, str) else ""


def _preview(text: str) -> str:
    first = next((line for line in text.splitlines() if line.strip()), "")
    if len(first) <= PREVIEW_CHARS:
        return first
    return first[:PREVIEW_CHARS].rstrip() + "…"
