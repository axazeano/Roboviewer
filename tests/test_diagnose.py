"""The verdict `--check-provider` reaches about tool calling.

This is the check that saves a user from eight agents failing one by one at the
same thing, so what it says has to be specific: not "tool calling is broken" but
which of the four ways it is broken, and what to put in the config. The exit
code is part of that — a script can gate on it.

The probes themselves need a gateway; the verdict does not, so it is tested on
its own. Neither does the header above it — which file the provider came from,
and where the key was found — so it is pinned here too.
"""

from __future__ import annotations

import pytest

from roboviewer.cli.check_provider import check_provider, report_tool_modes
from roboviewer.config import ProviderConfig, provider_config_path
from roboviewer.provider.probe import ProbeResult, Wire


def _called() -> ProbeResult:
    result = ProbeResult()
    result.tool_calls = ["pong"]
    return result


def _answered(content: str, finish_reason: str = "stop") -> ProbeResult:
    result = ProbeResult()
    result.content = content
    result.finish_reason = finish_reason
    return result


def _reasoning_cutoff() -> ProbeResult:
    result = ProbeResult()
    result.finish_reason = "length"
    result.reasoning = "First, the user wants the pong tool called, so I should"
    return result


def _modes(**by_key: ProbeResult) -> dict[str, ProbeResult]:
    return {key: by_key.get(key, _answered("pong")) for key in ("auto", "required", "forced")}


# ------------------------------------------------------------------ nothing works


def test_no_tool_call_at_all_fails_and_names_the_way_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert report_tool_modes(_modes()) == 1

    out = capsys.readouterr().out
    assert "no real tool_call at all" in out
    assert "another gateway" in out


def test_a_legacy_function_call_is_told_apart_from_silence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    legacy = _answered("")
    legacy.legacy_function_call = "pong"

    assert report_tool_modes(_modes(auto=legacy)) == 1
    assert "pre-June-2023 protocol" in capsys.readouterr().out


def test_a_call_retold_as_text_is_told_apart_from_silence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gateway does not parse tool_call, it narrates it — findings get lost
    in the text, which reads like a clean run."""
    assert report_tool_modes(_modes(auto=_answered('{"text": "pong"}'))) == 1
    assert "retells it in words" in capsys.readouterr().out


def test_running_out_of_tokens_mid_reasoning_is_not_blamed_on_the_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A reasoning model that spent the whole budget thinking told us nothing.
    Calling that "cannot do tool calling" is the false negative this guards."""
    cutoff = _reasoning_cutoff
    assert report_tool_modes(_modes(auto=cutoff(), required=cutoff(), forced=cutoff())) == 1

    out = capsys.readouterr().out
    assert "ran out while the model was reasoning" in out
    assert "not proof the model cannot call tools" in out
    assert "budget went to reasoning" in out  # the mode table names it too
    assert "no real tool_call at all" not in out
    assert "another gateway" not in out


# ------------------------------------------------------------------ partly works


def test_tool_calling_without_auto_is_not_enough(capsys: pytest.CaptureFixture[str]) -> None:
    """The reviewer runs on auto and forces a call only on the last turn."""
    assert report_tool_modes(_modes(required=_called(), forced=_called())) == 1
    assert 'not with tool_choice = "auto"' in capsys.readouterr().out


def test_no_forced_mode_prints_the_setting_to_use(capsys: pytest.CaptureFixture[str]) -> None:
    assert report_tool_modes(_modes(auto=_called(), required=_called())) == 0

    out = capsys.readouterr().out
    assert 'terminal_tool_choice = "required"' in out


def test_only_auto_warns_about_the_last_turn(capsys: pytest.CaptureFixture[str]) -> None:
    assert report_tool_modes(_modes(auto=_called())) == 0

    out = capsys.readouterr().out
    assert 'terminal_tool_choice = "auto"' in out
    assert "never called submit_findings" in out


# ------------------------------------------------------------------ works


def test_everything_working_keeps_the_default(capsys: pytest.CaptureFixture[str]) -> None:
    modes = _modes(auto=_called(), required=_called(), forced=_called())

    assert report_tool_modes(modes) == 0

    out = capsys.readouterr().out
    assert "supports tool calling" in out
    assert 'terminal_tool_choice = "forced"' in out
    assert "Put this in the config" not in out


def test_every_mode_is_listed_whatever_the_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    report_tool_modes(_modes(auto=_called()))

    out = capsys.readouterr().out
    for label in ('tool_choice = "auto"', 'tool_choice = "required"', "tool_choice = {function}"):
        assert label in out


# ------------------------------------------------------------------ the header


async def _failing_probe(
    provider: ProviderConfig, model: str, wire: Wire
) -> tuple[ProbeResult, dict[str, ProbeResult]]:
    plain = ProbeResult()
    plain.error = "HTTP 401"
    return plain, {}


def test_from_line_names_the_provider_file_when_there_is_one(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Which file is this configured in" and "where did the key come from" are
    two different answers; the header once printed the key's on both lines."""
    monkeypatch.delenv("ROBOVIEWER_API_KEY", raising=False)

    assert check_provider(ProviderConfig(), "m", source="/etc/roboviewer/provider.toml") == 2

    out = capsys.readouterr().out
    assert "from           /etc/roboviewer/provider.toml" in out
    assert "no file — on defaults" not in out
    assert "key            not found" in out


def test_from_line_falls_back_to_the_default_path_with_a_note(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ROBOVIEWER_API_KEY", raising=False)

    assert check_provider(ProviderConfig(), "m", source=None) == 2

    out = capsys.readouterr().out
    assert f"from           {provider_config_path()} [no file — on defaults]" in out


def test_key_line_names_the_key_source_not_the_config_file(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("roboviewer.cli.check_provider.probe_all", _failing_probe)

    code = check_provider(
        ProviderConfig(api_key="sk-test"), "m", source="/etc/roboviewer/provider.toml"
    )

    assert code == 1  # the stubbed plain probe fails; the header is what is under test
    out = capsys.readouterr().out
    assert "from           /etc/roboviewer/provider.toml" in out
    assert "key            provider.api_key from the config" in out
    assert "sk-test" not in out
