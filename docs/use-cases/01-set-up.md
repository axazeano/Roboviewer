# Set it up against a gateway

Someone has a corporate OpenAI-compatible endpoint and wants a first review to
come out of it. They have a URL, a key, and a model name from whoever runs the
gateway.

## What serves it

| Surface | Part |
| --- | --- |
| `--config PATH` | read this file instead of `~/.config/roboviewer/config.toml` |
| `provider.base_url`, `provider.model` | the two settings that have no working default |
| `provider.api_key_env`, `provider.api_key` | key from the environment, or inline while debugging |
| `provider.auth_header`, `provider.auth_scheme` | gateways that do not want `Authorization: Bearer` |
| `provider.extra_headers` | whatever else the gateway demands of every request |
| `--show-config` | what is in effect, and which file it came from |

## What it costs to learn

Two settings, and everything else has a default that works. The install
instructions are four lines and the first run needs nothing but a target
branch.

The auth block is the part that costs: `auth_header`, `auth_scheme`,
`extra_headers` and the two key settings are five ideas for one job — proving
who you are. They exist because gateways genuinely differ, and someone facing a
gateway that wants `X-API-Key` with no scheme cannot proceed without them. But
a person meets all five while looking for the one that applies to them.

`--config` is cheap since TASK-14: one file, named or default, and
`--show-config` prints which. It used to be three files merged key by key.

## Verdict: keep

Proportionate. The auth settings look like clutter only until you meet a
gateway that needs them, and `--check-provider` (case 2) exists precisely so
that nobody has to guess which of the five is wrong.

One thing worth doing that is not a removal: the five auth settings are
documented in `config.example.toml` as five entries rather than as one section
with the two or three combinations that actually occur. That is a
documentation shape, not a surface.
