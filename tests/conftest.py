"""Shared fixtures: a diff that never touches git, and a runner that never
touches the network."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from roboviewer.config import Config
from roboviewer.models import DiffStat, Usage
from roboviewer.provider import AgentOutcome, AgentRequest, Runner
from roboviewer.repo import Attachments, ChangeSet, Comparison

ANNOTATED = """\
### src/cart.py
```
   41   | def apply(self, code):
   42 + |     self.total -= discount(code)
```
"""


@pytest.fixture(autouse=True)
def _no_ambient_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test inherits a login from the machine it runs on.

    The corpus client takes a token from the environment and, failing that, from
    whatever `gh` is logged in as. Both are ambient: with a token the client
    takes the GraphQL path and a real one sends real requests, so the same test
    passes on a laptop and fails in CI, or worse, quietly goes to the network.
    A test that wants a token asks for one explicitly.
    """
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "measure.corpus.github._run_gh",
        lambda command: subprocess.CompletedProcess(command, 1, stdout="", stderr=""),
    )


def make_bundle(root: Path, **overrides: Any) -> ChangeSet:
    """A change set that never touched git. Overrides name `ChangeSet` fields
    (`files`, `lines`, `references`) or `Attachments` ones (`annotated`, ...)."""
    attachments = Attachments(
        annotated=ANNOTATED, inlined=["src/cart.py"], fallback=[], hunks="", hunks_truncated=False
    )
    fields: dict[str, Any] = {
        "files": [DiffStat(file="src/cart.py", status="M", added=1, removed=0)],
        "lines": {},
        "references": None,
    }
    for name, value in overrides.items():
        if hasattr(attachments, name):
            setattr(attachments, name, value)
        else:
            fields[name] = value
    return ChangeSet(
        comparison=Comparison(
            root=root,
            source="feature/x",
            source_ref="feature/x",
            target="develop",
            base_sha="abcdef1234567890",
            head_sha="1234567890abcdef",
            detached=False,
        ),
        attachments=attachments,
        **fields,
    )


def ok_outcome(**payload: Any) -> AgentOutcome:
    body = {"summary": "", "findings": []}
    body.update(payload)
    return AgentOutcome(payload=body, usage=Usage(), turns=1)


class ScriptedRunner(Runner):
    """Returns a canned outcome and keeps every request it was handed, so a test
    can assert on the prompt the agent would actually have received."""

    name = "scripted"

    def __init__(self, *outcomes: AgentOutcome) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[AgentRequest] = []

    async def run(self, request: AgentRequest) -> AgentOutcome:
        self.requests.append(request)
        if len(self._outcomes) > 1:
            return self._outcomes.pop(0)
        return self._outcomes[0]


@pytest.fixture
def config() -> Config:
    cfg = Config()
    cfg.reviewer.model = "test-model"
    cfg.run.enable_judge = False
    return cfg
