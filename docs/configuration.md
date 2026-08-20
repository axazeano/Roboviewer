# Configuration

Where the two config files live, what their sections mean, and how to find out
whether the gateway on the other end can actually run a review.

## Two files

```bash
mkdir -p ~/.config/roboviewer
cp provider.example.toml ~/.config/roboviewer/provider.toml
cp config.example.toml   ~/.config/roboviewer/config.toml
```

`provider.toml` is how to reach the gateway — address, key, auth. It is set once
per machine and then left alone.

`config.toml` is what to ask of a model: `[reviewer]`, `[judge]` and `[run]`.
This is the half you turn while fitting the tool to a model, and the half that
gets copied — into an experiment, into a write-up that records how a run was
configured. Leave `[judge]` out entirely and the judge runs on the reviewer's
settings; that absence is the only way to say "the same", so no value in the
file secretly means "inherit".

The split is not tidiness. While the two lived in one file, every copy of the
settings carried the key along with them. Now the file people pass around cannot
hold one: **a `--config` file containing a `[provider]` section is refused**, and
the error says where the provider belongs.

`--config PATH` reads a different settings file **instead of** the one in
`~/.config/roboviewer/`, not on top of it — but it never changes where the
provider comes from. `--show-config` prints both files and which half came from
which.

### Coming from a single file

A `config.toml` that still has a `[provider]` section keeps working, and every
run says how to split it. Move the section into `provider.toml`, delete it from
`config.toml`, and the notice stops. If both files carry a provider, the one in
`provider.toml` wins and the leftover section is called out rather than silently
ignored.

Prompts and templates follow the opposite rule, even though `.roboviewer/` holds
all three — see [Tuning](tuning.md).

## Check the gateway

```bash
roboviewer --check-provider
```

It makes a handful of targeted requests and names what is wrong — wrong auth
scheme, a `base_url` missing `/v1`, a gateway that cannot do tool calling —
instead of leaving you to infer it from eight agents failing at once.

## Rate limits

A review is a burst — eight agents, each resending a large context every turn —
and serverless providers meter that per minute. Nothing needs configuring for
the usual case: a `429` (or a `503`, which is the same message with a different
number) holds *every* agent back rather than only the one that was refused, for
as long as the provider asks, and providers that advertise their current
ceilings in the response headers have those adopted automatically. Set
`[provider.rate_limits]` when yours does not advertise, or to stay deliberately
under what you are entitled to; the buckets are separate because providers meter
them separately, and the tightest one is usually uncached prompt tokens — which
is exactly what a shared prompt prefix exists to keep low. A run that is being
paced says so (`waited 12s on uncached prompt tokens`) rather than looking like
a hang.
