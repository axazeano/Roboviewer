"""Talking to the model's gateway.

`runner` is the contract the review asks of a provider — run one agent, hand
back what it submitted — and `openai_agent` the tool-calling loop that fulfils
it over any OpenAI-compatible chat-completions API. Around the loop: `request`
shapes a request from the config, `usage` reads token counts out of an answer,
`ratelimit` paces the run so the gateway does not have to say no, and `probe`
makes the one diagnostic request `--check-provider` is built on.
"""

from __future__ import annotations

from .openai_agent import OpenAIAgentRunner
from .runner import AgentOutcome, AgentRequest, Runner

__all__ = ["AgentOutcome", "AgentRequest", "OpenAIAgentRunner", "Runner"]
