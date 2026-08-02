"""Agent runner abstraction.

The pipeline does not care who executes the agent: an OpenAI-compatible API, a
local model, or someone else's CLI. All it needs is the terminal tool's payload.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from ..models import Usage

# (event, detail) — forwarded to the console: tool calls, retries, errors
ProgressHook = Callable[[str, str], None]


@dataclass
class AgentRequest:
    system: str
    prompt: str
    tools: list[dict[str, Any]]
    terminal_tool: dict[str, Any]
    model: str
    max_turns: int = 25
    # Reasoning mode for this agent; the judge and the reviewers may differ.
    # None leaves the model on its own default.
    enable_thinking: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal_name(self) -> str:
        return str(self.terminal_tool["function"]["name"])


@dataclass
class AgentOutcome:
    payload: dict[str, Any] | None
    usage: Usage
    turns: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.payload is not None


class Runner(ABC):
    """Executor of a single agent run."""

    name: str = "base"

    @abstractmethod
    async def run(self, request: AgentRequest, on_progress: ProgressHook | None = None) -> AgentOutcome:
        ...

    async def aclose(self) -> None:
        return None
