"""What the probe reads off a completion, pinned without a gateway.

One distinction carries the whole diagnosis: a reasoning model cut off by
max_tokens looks, to a naive reading, exactly like a model that answered with
empty text — finish_reason and the reasoning field on the message are what
tell "ran out of budget" apart from "answered instead of calling".
"""

from __future__ import annotations

from typing import Any

from openai.types.chat.chat_completion import Choice

from roboviewer.provider.probe import ProbeResult


def _choice(message: dict[str, Any], finish_reason: str = "stop") -> Choice:
    return Choice.model_validate(
        {"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", **message}}
    )


def test_reasoning_cut_off_by_the_budget_is_recognised() -> None:
    result = ProbeResult.from_choice(
        _choice({"content": None, "reasoning_content": "The user wants"}, finish_reason="length")
    )

    assert result.ran_out_while_reasoning
    assert "budget went to reasoning" in result.summary()


def test_a_tool_call_after_reasoning_is_a_call_like_any_other() -> None:
    message = {
        "content": None,
        "reasoning_content": "The user wants the pong tool called.",
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "pong", "arguments": "{}"}}
        ],
    }

    result = ProbeResult.from_choice(_choice(message, finish_reason="tool_calls"))

    assert result.tool_calls == ["pong"]
    assert not result.ran_out_while_reasoning


def test_the_reasoning_field_dialect_does_not_matter() -> None:
    # some gateways send `reasoning` instead of `reasoning_content`
    result = ProbeResult.from_choice(
        _choice({"content": None, "reasoning": "hmm"}, finish_reason="length")
    )

    assert result.ran_out_while_reasoning


def test_cut_off_without_any_reasoning_stays_a_plain_text_verdict() -> None:
    result = ProbeResult.from_choice(_choice({"content": "I will now"}, finish_reason="length"))

    assert not result.ran_out_while_reasoning
    assert "plain text" in result.summary()
