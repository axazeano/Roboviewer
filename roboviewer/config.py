"""Configuration loading.

One file, and the file you can point at:

    built-in defaults
    → ~/.config/roboviewer/config.toml, or the file --config names instead
    → CLI flags

`--config` replaces rather than adds. Settings used to arrive from three files
merged key by key, which meant the value in effect was written down nowhere: to
answer "which endpoint is this run using" you had to read three files and
reproduce the merge. Flags over a file stay, because a flag is visible in the
command that produced the run.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import metering

# A key nobody reads is a setting nobody applied, and ignoring it silently — the
# pydantic default — means a typo leaves the run on defaults while the person
# who wrote it believes otherwise. Set on every model: a stray key inside
# [provider] is checked by ProviderConfig, not by the outer one.
STRICT = ConfigDict(extra="forbid")

try:  # private SDK API: the only way to drop the built-in Authorization header
    from openai._types import Omit as _OmitType

    _OMIT: Any = _OmitType()
except ImportError:  # pragma: no cover — in case SDK internals change
    _OMIT = None

# Stack-agnostic on purpose: lockfiles, generated code, vendored dependencies and
# snapshots. Anything language-specific belongs in the user's config — see
# config.example.toml for ready-made blocks.
DEFAULT_EXCLUDES = [
    "*.lock",
    "**/*.generated.*",
    "**/generated/**",
    "**/node_modules/**",
    "**/vendor/**",
    "**/*.snap",
    "**/__snapshots__/**",
    # Our own reports: the default output_dir sits inside the reviewed repository,
    # and once committed they would feed every later run its own past output.
    ".roboviewer/**",
    "**/.roboviewer/**",
]


class ModelConfig(BaseModel):
    """What to ask of a model — one of these per role.

    Separate from `ProviderConfig` because the two are set by different people
    at different times: the gateway is configured once by whoever runs it, and
    this is what gets turned while fitting the tool to a model.
    """

    model_config = STRICT

    model: str = "gpt-4o"
    temperature: float = 0.1
    max_tokens: int = 8000
    max_turns: int = 25
    # None sends nothing and leaves the model on its own default; False sends
    # chat_template_kwargs.enable_thinking = false, which Qwen-style chat
    # templates understand without breaking tool calling.
    enable_thinking: bool | None = None
    # Fields the SDK has no typed parameter for, merged into the body as-is.
    # `enable_thinking` wins over a colliding key. Here rather than on the
    # provider because it carries the thinking switch, which is per role.
    extra_body: dict[str, Any] = Field(default_factory=dict)

    def request_body(self) -> dict[str, Any]:
        body: dict[str, Any] = dict(self.extra_body)
        if self.enable_thinking is not None:
            template_kwargs = dict(body.get("chat_template_kwargs") or {})
            template_kwargs["enable_thinking"] = self.enable_thinking
            body["chat_template_kwargs"] = template_kwargs
        return body


class RateLimits(BaseModel):
    """What the gateway will take per minute, so a run paces itself.

    An absent bucket means "not known", not "none": the window is simply not
    enforced. Which is usually the right starting point — most gateways report
    their effective ceilings on every response and `adopt_advertised` picks
    those up, so most people never fill this in at all.

    The bucket names are the ones this gateway meters, and nothing else is
    accepted: a ceiling written under a name the gateway does not count is a
    setting that would never have applied, and finding that out at load time is
    the point. `roboviewer --check-provider` prints the names in force.
    """

    model_config = STRICT

    # Which gateway family this is. "auto" reads it off base_url, which is
    # enough for the gateways whose metering differs from the compatible norm;
    # name one explicitly when running behind a proxy that hides the host.
    metering: Literal["auto", "openai", "fireworks", "anthropic", "none"] = "auto"
    # Bucket name → tokens (or requests) per minute. See `metering` for the
    # names each family uses.
    per_minute: dict[str, int] = Field(default_factory=dict)
    # Read the ceilings, and what is left of them, out of the response headers
    # where the gateway sends them. On an adaptive plan the advertised number is
    # the true one and a figure written here months ago is not.
    adopt_advertised: bool = True


class ProviderConfig(BaseModel):
    """How to reach the gateway. What to ask of it is `ModelConfig`."""

    model_config = STRICT

    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "ROBOVIEWER_API_KEY"
    # Key inlined in the config: takes precedence over the environment variable.
    # Handy while debugging, but such a file must never reach git.
    api_key: str | None = None
    timeout_s: float = 300.0
    max_retries: int = 3
    # Some gateways cannot handle several tool_calls in one response.
    parallel_tool_calls: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # How much of the provider a run may take per minute. The cooldown after a
    # 429 needs nothing set here; the ceilings are what this configures.
    rate_limits: RateLimits = Field(default_factory=RateLimits)

    @model_validator(mode="after")
    def _check_buckets(self) -> ProviderConfig:
        """A ceiling has to name a bucket this gateway actually meters.

        Checked here rather than on `RateLimits` because which names are valid
        depends on `base_url`, and a model only sees its own fields.
        """
        meter, _ = metering.resolve(self.rate_limits.metering, self.base_url)
        unknown = sorted(set(self.rate_limits.per_minute) - set(meter.names()))
        if not unknown:
            return self
        known = ", ".join(meter.names()) or "none — this gateway meters nothing per key"
        raise ValueError(
            f"provider.rate_limits.per_minute: {', '.join(unknown)} "
            f"{'is' if len(unknown) == 1 else 'are'} not metered by the "
            f"{meter.name} gateway. Buckets it does meter: {known}"
        )

    def meter(self) -> tuple[metering.Meter, str]:
        """(what this gateway meters, how that was decided)."""
        return metering.resolve(self.rate_limits.metering, self.base_url)

    # Defaults to the OpenAI way, Authorization: Bearer <key>; gateways often
    # want something else. See config.example.toml for the combinations.
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"

    # How hard the agent is pushed to submit its result on the final turn.
    # Lower it only when `--check-provider` says the gateway rejects the
    # stronger mode — it prints the value to use.
    terminal_tool_choice: Literal["forced", "required", "auto"] = "forced"

    def api_key_source(self) -> tuple[str | None, str]:
        """(key, where it came from) — the source is what matters when debugging 401."""
        if self.api_key:
            return self.api_key, "provider.api_key from the config"
        from_env = os.environ.get(self.api_key_env)
        if from_env:
            return from_env, f"environment variable {self.api_key_env}"
        return None, "not found"

    def resolve_api_key(self) -> str:
        key, _ = self.api_key_source()
        if not key:
            raise RuntimeError(
                f"No API key found. Set the {self.api_key_env} environment variable "
                f"or provider.api_key in the config."
            )
        return key

    def auth_value(self) -> str:
        key = self.resolve_api_key()
        return f"{self.auth_scheme} {key}" if self.auth_scheme else key

    def request_headers(self) -> dict[str, Any]:
        """Headers attached to every request.

        Passed per-request rather than via default_headers: dropping the built-in
        Authorization header is only possible with the Omit sentinel, and the SDK
        client constructor rejects it.
        """
        headers: dict[str, Any] = dict(self.extra_headers)
        headers[self.auth_header] = self.auth_value()
        if self.auth_header.lower() != "authorization" and _OMIT is not None:
            headers["Authorization"] = _OMIT
        return headers

    def masked_key(self) -> str:
        key, _ = self.api_key_source()
        if not key:
            return "—"
        # Show the tail so two similar keys can be told apart without revealing either.
        if len(key) <= 12:
            return f"(short, {len(key)} chars)"
        return f"{key[:4]}…{key[-4:]} ({len(key)} chars)"

    def terminal_tool_choice_value(self, tool_name: str) -> Any:
        if self.terminal_tool_choice == "forced":
            return {"type": "function", "function": {"name": tool_name}}
        return self.terminal_tool_choice  # "required" / "auto"


class RunConfig(BaseModel):
    model_config = STRICT

    # Branches are deliberately absent: the target is always a CLI argument and
    # the source defaults to the current branch.
    checklist_dir: str = "checklists/default"
    # Empty → the bundled set, plus .roboviewer/prompts/ inside the reviewed
    # repository when it exists. A custom set carries only the files it changes.
    prompts_dir: str = ""
    # Resolved the same way as prompts_dir.
    templates_dir: str = ""
    # A format is a module in `renders` — see known() there — or a bare
    # report.<name>.j2 in templates_dir. Overridden by --format.
    report_formats: list[str] = Field(default_factory=lambda: ["md"])
    # Language the model writes findings in. Empty asks for nothing, so it
    # answers in the language of the prompts. An ISO code or a name; anything
    # unrecognised goes into the prompt as written. Overridden by --language.
    output_language: str = ""
    output_dir: str = ".roboviewer/runs"
    # Checklist items reviewed at the same time — concurrent requests to the model,
    # not OS threads.
    concurrency: int = 4
    # Changed files go to the agent in full with changed lines marked up.
    # Anything longer falls back to hunks; the agent reads the rest via read_file.
    inline_max_lines: int = 600
    # Overall cap on inlined files; the budget goes to the most heavily changed ones.
    inline_max_total_chars: int = 400_000
    # Hunk context for files that were not inlined.
    diff_context_lines: int = 12
    diff_max_chars: int = 300_000
    # Setting this in a config REPLACES the defaults rather than extending them —
    # copy DEFAULT_EXCLUDES into your list if you still want them.
    exclude_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    # Keep only findings that point at what the MR changed. Reviewers are given
    # changed files in full — which is what stops them inventing missing handling
    # that sits twenty lines above — and the cost of that is a standing
    # temptation to report the untouched 98% of the file. Line arithmetic, so it
    # holds for any language. What it drops is listed in the report, not lost.
    enforce_scope: bool = True
    # How far from a changed line a finding may sit and still count as being
    # about the change. Covers the declaration a few lines above an edit and the
    # neighbour of a deleted block. Raising it lets more pre-existing code back in.
    scope_margin: int = 5
    enable_judge: bool = True
    # "batch" — one pass over the whole list, the cheap default.
    # "two_stage" — a pass per finding, then one pass over what survived. The
    # split gives each claim a whole turn budget and limits a failure to one
    # verdict; the second pass buys back the cross-finding view it gives up,
    # because severity is comparative and a pass holding one claim has no scale
    # to judge it against.
    judge_mode: Literal["batch", "two_stage"] = "batch"
    # Search the tree once, before the fan-out, for references the diff
    # introduces that resolve to nothing — missing symbols, storyboards,
    # localization keys, unconnected outlets, files no build manifest mentions.
    # The result goes into the shared context, so it costs one prefix rather
    # than one lookup per agent. It reads the files `exclude_globs` drops.
    resolve_references: bool = True
    # Maximum lines returned by a single read_file call.
    max_read_lines: int = 800
    # Severity at which the run exits 1, so a CI job can go red on it. Counts
    # confirmed findings that are in scope; "never" reports and exits 0.
    # Overridden by --fail-on.
    fail_on: Literal["never", "blocker", "major", "minor", "nit"] = "never"

class Config(BaseModel):
    model_config = STRICT

    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    reviewer: ModelConfig = Field(default_factory=ModelConfig)
    # No [judge] section means the judge runs on the reviewer's settings. The
    # rule is here, once, rather than as a fallback inside each field: three
    # fields with three different spellings of "unset" is what this replaced.
    judge: ModelConfig | None = None
    run: RunConfig = Field(default_factory=RunConfig)
    # The file this came from; None means nothing but the defaults below.
    source: str | None = None

    def for_judge(self) -> ModelConfig:
        return self.judge or self.reviewer


# Settings that used to live somewhere else. `extra="forbid"` already stops a
# config in the old shape, but "not permitted" only says a key is wrong — for a
# key that moved, the useful half of the answer is where it went.
MOVED: dict[str, str] = {
    "provider.model": "reviewer.model",
    "provider.temperature": "reviewer.temperature",
    "provider.max_tokens": "reviewer.max_tokens",
    "provider.enable_thinking": "reviewer.enable_thinking",
    "provider.extra_body": "reviewer.extra_body",
    "provider.judge_model": "judge.model, in a [judge] section of its own",
    "provider.judge_enable_thinking": "judge.enable_thinking, in a [judge] section",
    "run.max_turns": "reviewer.max_turns",
    "run.judge_max_turns": "judge.max_turns, in a [judge] section",
    "run.min_confidence": "gone — the judge and the scope gate decide what survives",
    # The four fixed buckets were one gateway's spelling. They are now keys in a
    # map, under whichever names the gateway in use actually meters.
    "provider.rate_limits.prompt_tokens_per_minute": (
        'provider.rate_limits.per_minute, as "prompt tokens" (fireworks) or "tokens" (openai)'
    ),
    "provider.rate_limits.uncached_prompt_tokens_per_minute": (
        'provider.rate_limits.per_minute, as "uncached prompt tokens" (fireworks) '
        'or "input tokens" (anthropic)'
    ),
    "provider.rate_limits.generated_tokens_per_minute": (
        'provider.rate_limits.per_minute, as "generated tokens" (fireworks) '
        'or "output tokens" (anthropic)'
    ),
    "provider.rate_limits.requests_per_minute": (
        'provider.rate_limits.per_minute, as "requests"'
    ),
}


def home_config_path() -> Path:
    return Path.home() / ".config" / "roboviewer" / "config.toml"


def _moved_hint(exc: ValidationError) -> str:
    """Names the new home of every moved key the file still uses."""
    hits = [
        f"  {key} is now {MOVED[key]}"
        for error in exc.errors()
        if (key := ".".join(str(part) for part in error["loc"])) in MOVED
    ]
    if not hits:
        return ""
    return "\n\nSettings moved:\n" + "\n".join(hits)


def load_config(explicit: Path | None = None) -> Config:
    """The one file a run reads, or the defaults when there is none.

    An absent home file is normal — the defaults are a working configuration
    apart from the provider. An absent `--config` is a typo: somebody named a
    file, and quietly running on something else is the wrong kindness.
    """
    if explicit is None:
        path = home_config_path()
        if not path.is_file():
            return Config()
    else:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Config not found: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    try:
        cfg = Config.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{exc}{_moved_hint(exc)}") from exc
    cfg.source = str(path)
    return cfg
