"""What the review asks of a provider: run one agent, hand back what it submitted.

The pipeline does not care who executes the agent — an OpenAI-compatible API, a
local model, someone else's CLI. All it needs is the terminal tool's payload,
and `Runner` is that contract. `openai_agent` is the one implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..config import ModelConfig
from ..models import Usage
from ..observer import SILENT, AgentObserver


@dataclass
class AgentRequest:
    system: str
    prompt: str
    tools: list[dict[str, Any]]
    terminal_tool: dict[str, Any]
    # What to ask of the model, carried per request rather than held by the
    # runner: the judge and the reviewers may be configured differently, down
    # to temperature and the request body.
    settings: ModelConfig
    metadata: dict[str, Any] = field(default_factory=dict)
    # Whoever is keeping an account of what this agent does — its prompts, its
    # turns, its tool calls, and the runner's own notes between them. Silence by
    # default: the tool keeps none of it, and a runner reports the same way
    # regardless.
    observer: AgentObserver = SILENT

    @property
    def terminal_name(self) -> str:
        return str(self.terminal_tool["function"]["name"])


@dataclass
class AgentOutcome:
    payload: dict[str, Any] | None
    usage: Usage
    turns: int
    error: str | None = None
    # The agent submitted because the turn limit forced it, not because it was
    # done. The payload is real but the review behind it is half-finished, and a
    # report that hides that reads as "nothing found here".
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.payload is not None


class Runner(ABC):
    """Executor of a single agent run."""

    name: str = "base"

    @abstractmethod
    async def run(self, request: AgentRequest) -> AgentOutcome:
        ...

    async def aclose(self) -> None:
        return None
