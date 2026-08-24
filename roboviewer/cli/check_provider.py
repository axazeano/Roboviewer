"""What `--check-provider` prints, and the exit code its verdict implies.

The requests themselves are `provider.probe`; this is the reading of them: which
of the four ways tool calling can be broken this gateway shows, and what to put
in the config about it.
"""

from __future__ import annotations

import asyncio

from ..config import ProviderConfig, provider_config_path
from ..provider.probe import MAX_TOKENS, TOOL_MODES, ProbeResult, Wire, mask_headers, probe_all


def check_provider(provider: ProviderConfig, model: str, source: str | None = None) -> int:
    """The model is passed in rather than read off the provider: reaching the
    gateway and choosing what to ask it are two settings now, and the probe
    needs one name out of the second."""
    key, source = provider.api_key_source()

    print("Provider")
    print(f"  from           {source or provider_config_path()} [no file — on defaults]")
    print(f"  base_url       {provider.base_url}")
    print(f"  model          {model}")
    print(f"  key            {source}")
    print(f"  auth           {provider.auth_header}: "
          f"{(provider.auth_scheme + ' ') if provider.auth_scheme else ''}<key>")
    print(f"  submission     terminal_tool_choice = \"{provider.terminal_tool_choice}\"")
    if provider.extra_headers:
        print(f"  extra headers  {mask_headers(provider.extra_headers)}")
    print()

    if key is None:
        print("✗ No key found — there is nothing to make a request with.")
        return 2
    if key != key.strip():
        print("⚠ The key has spaces or a newline at its edges — a common cause of 401.")

    wire = Wire()
    plain, modes = asyncio.run(probe_all(provider, model, wire))

    print("1. Plain request")
    if plain.ok:
        # For a plain request text is exactly what we want, so no call-shape verdict here
        print(f"   ✓ got an answer: {plain.content.strip()[:80] or '(empty)'}")
    else:
        print(f"   ✗ {plain.summary()}")
    if not plain.ok:
        print()
        _dump_wire(wire)
        print()
        _print_auth_hints()
        return 1

    print()
    print("2. Tool call — this is how the reviewer submits its result")
    return report_tool_modes(modes)


def report_tool_modes(modes: dict[str, ProbeResult]) -> int:
    """Prints what each tool_choice mode did and the verdict that follows from
    it; returns the exit code that verdict implies — 1 when the gateway cannot
    run a review."""
    _print_mode_table(modes)
    print()

    working = [key for key, _, _ in TOOL_MODES if modes[key].tool_calls]
    if not working:
        _explain_no_tool_calls(modes)
        return 1
    # The reviewer needs "auto" during the run and the terminal mode on the last turn.
    if "auto" not in working:
        _explain_auto_missing()
        return 1

    _recommend_terminal_choice(working)
    return 0


def _dump_wire(wire: Wire) -> None:
    print("Request as it went over the wire:")
    print(f"  {wire.method} {wire.url}")
    skip = ("host", "accept-encoding", "connection", "content-length", "accept", "user-agent")
    for name, value in mask_headers(wire.request_headers).items():
        low = name.lower()
        # x-stainless-* is SDK telemetry, unrelated to authentication
        if low in skip or low.startswith("x-stainless-"):
            continue
        print(f"  {name}: {value}")
    if wire.status is not None:
        print()
        print(f"Response: HTTP {wire.status}")
        for name in ("www-authenticate", "x-request-id", "x-error", "server", "content-type"):
            if name in wire.response_headers:
                print(f"  {name}: {wire.response_headers[name]}")


def _print_auth_hints() -> None:
    print("Compare this with a request that works for you by hand. If the shape of")
    print("the authentication differs, set provider.auth_header / provider.auth_scheme:")
    print('  auth_header = "api-key",   auth_scheme = ""       → api-key: <key>')
    print('  auth_header = "X-Api-Key", auth_scheme = ""       → X-Api-Key: <key>')
    print('  auth_scheme = "Token"                             → Authorization: Token <key>')


def _print_mode_table(modes: dict[str, ProbeResult]) -> None:
    width = max(len(label) for _, label, _ in TOOL_MODES)
    for key, label, _ in TOOL_MODES:
        result = modes[key]
        mark = "✓" if result.tool_calls else "✗"
        print(f"   {mark} {label:<{width}}  {result.summary()}")


def _explain_no_tool_calls(modes: dict[str, ProbeResult]) -> None:
    """Ways to end up without a tool call, and only the wording tells them
    apart — which is the whole reason for probing before a run. One of them,
    running out of tokens mid-reasoning, is not a verdict on the model at all."""
    if any(modes[k].ran_out_while_reasoning for k in modes):
        print("Verdict: inconclusive — the token budget ran out while the model was reasoning.")
        print(f"  The model reasons before answering, and {MAX_TOKENS} tokens were not enough")
        print("  to finish. That is a budget problem, not proof the model cannot call tools.")
        print("  What to do: send the same request by hand with a larger max_tokens; if a")
        print("  tool_call comes back, the model is fine and only this probe fell short.")
        return
    print("Verdict: the gateway returned no real tool_call at all.")
    if any(modes[k].legacy_function_call for k in modes):
        print("  A legacy function_call field arrived instead of tool_calls — the gateway")
        print("  speaks the pre-June-2023 protocol. The reviewer will not understand it.")
    elif any(modes[k].content_looks_like_call for k in modes):
        print("  Instead of a call, text that looks like one arrived: the gateway does not")
        print("  parse tool_call, it just retells it in words. The reviewer will try to pull")
        print("  JSON out of the text, but it will not be reliable — findings will be lost.")
    else:
        print("  The model simply answered with text. Either it cannot do tool calling, or")
        print("  the gateway drops the tools field from the request.")
    print("  What to do: take a model that supports tool calling, or another gateway.")


def _explain_auto_missing() -> None:
    print('Verdict: tool calling works, but not with tool_choice = "auto".')
    print("  The reviewer runs on auto: it decides for itself whether to read files, and")
    print("  forces a call only on the last turn. This gateway will not do.")


def _recommend_terminal_choice(working: list[str]) -> None:
    """The strongest mode the gateway actually supports, and what it costs when
    that is not the default."""
    best = next(key for key in ("forced", "required", "auto") if key in working)
    print("Verdict: the gateway supports tool calling.")
    if best == "forced":
        print('  The default setting fits: terminal_tool_choice = "forced".')
        return

    print('  But "forced" mode is not supported. Put this in the config:')
    print("    [provider]")
    print(f'    terminal_tool_choice = "{best}"')
    if best == "auto":
        print("  On auto the reviewer cannot make the agent submit its result on the last")
        print('  turn — some items will fail with "the model never called submit_findings".')
        print("  Raising max_turns helps.")
