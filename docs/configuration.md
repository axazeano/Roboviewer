# Configuration

Where the config file lives, what its sections mean, and how to find out whether
the gateway on the other end can actually run a review.

## The file

```bash
mkdir -p ~/.config/roboviewer
cp config.example.toml ~/.config/roboviewer/config.toml
export ROBOVIEWER_API_KEY=...
```

Set `provider.base_url` and `reviewer.model`. Everything else has working
defaults and is documented inline in
[config.example.toml](../config.example.toml).

The file has two kinds of section. `[provider]` is how to reach the gateway —
address, key, auth — and is usually set once by whoever runs it. `[reviewer]`
and `[judge]` are what to ask of a model, and are what you turn while fitting
the tool to one. Leave `[judge]` out entirely and the judge runs on the
reviewer's settings; that absence is the only way to say "the same", so no
value in the file secretly means "inherit".

That path is the only file a run reads on its own. `--config PATH` reads a
different one **instead of** it, not on top of it, so the file you name carries
everything the run needs. `--show-config` prints which file is in use.

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
