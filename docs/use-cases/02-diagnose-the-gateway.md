# Find out why the gateway does not answer

The first run failed, or eight agents failed at once, and the person needs to
know whether the fault is the URL, the key, the auth scheme, or a gateway that
cannot do tool calling at all.

## What serves it

| Surface | Part |
| --- | --- |
| `--check-provider` | a handful of targeted probes, then a verdict |
| `provider.timeout_s`, `provider.max_retries` | how long a probe waits and how often it tries |
| `provider.terminal_tool_choice` | how hard the final turn is pushed to submit; a gateway may reject the strong form |
| `provider.parallel_tool_calls` | some gateways cannot take several tool calls in one response |
| `provider.extra_body` | fields the SDK has no typed parameter for |

## What it costs to learn

One flag. `--check-provider` names what is wrong instead of leaving it to be
inferred, and where a setting is the answer it prints the value to use — that
is the whole point of the case, and it is why `terminal_tool_choice` is
tolerable as a setting: nobody has to discover it, the diagnostic hands it
over.

`extra_body` is the escape hatch, and escape hatches are cheap to keep and
expensive to remove: it is the only way to pass a gateway-specific field the
SDK does not model, and its absence would mean a code change per gateway.

## Verdict: keep

The strongest case in the set. It converts a class of failure that would
otherwise be read as "the tool is broken" into a named cause, and it is one
flag with no prerequisites.

The stale bit is documentation, not surface: `--check-provider` help text and
the README both describe it well, but `--show-config`, which people reach for
in the same situation, still describes config stacking that no longer exists.
Filed separately.
