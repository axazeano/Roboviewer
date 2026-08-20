"""Configuration loading.

Two files, split by how long a setting lives:

    provider.toml   the gateway and its key — set once per machine
    config.toml     reviewer, judge and run — turned constantly, and copied
                    into experiments and write-ups along the way

    built-in defaults
    → ~/.config/roboviewer/provider.toml          (the provider, always)
    → ~/.config/roboviewer/config.toml, or --config (everything else)
    → CLI flags

The split is not tidiness. The settings half gets copied wherever a run has to
be written down; while the credentials shared a file with it, every copy
carried the key. A `[provider]` section in a `--config` file is refused, so the
file people pass around cannot hold a secret in the first place.

`--config` still replaces rather than adds, for the half it covers. Settings
used to arrive from three files merged key by key, which meant the value in
effect was written down nowhere: to answer "which endpoint is this run using"
you had to read three files and reproduce the merge. Flags over a file stay,
because a flag is visible in the command that produced the run.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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

    Zero means "not known", not "none": the window is simply not enforced. Which
    is usually the right starting point — serverless providers advertise their
    effective ceilings on every response, and `adopt_advertised` picks those up,
    so most people never fill these in.

    Counted separately because providers meter them separately. Fireworks caps
    total prompt tokens, uncached prompt tokens and generated tokens, and the
    uncached one is the tightest by a factor of four — which is exactly the one
    a shared prompt prefix is designed to stay under.
    """

    model_config = STRICT

    prompt_tokens_per_minute: int = 0
    uncached_prompt_tokens_per_minute: int = 0
    generated_tokens_per_minute: int = 0
    # Some gateways count requests rather than tokens.
    requests_per_minute: int = 0
    # Read the ceilings out of the response headers where the provider sends
    # them. On an adaptive plan the advertised number is the true one and a
    # figure written here months ago is not.
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
    # The file the settings came from; None means nothing but the defaults below.
    source: str | None = None
    # The file the provider came from, which is a different file and may be
    # absent independently. Excluded from the model's own validation: both are
    # set after parsing, by the loader that knows which file it read.
    provider_source: str | None = None
    # Set when the provider was found in the legacy combined file. Carried on
    # the config rather than printed at load time so the CLI decides where a
    # notice belongs, and nothing prints from inside a parser.
    provider_notice: str | None = None

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
}


def home_config_path() -> Path:
    return Path.home() / ".config" / "roboviewer" / "config.toml"


PROVIDER_CONFIG_ENV = "ROBOVIEWER_PROVIDER_CONFIG"


def provider_config_path() -> Path:
    """Where the provider is read from.

    A CI runner and a container have no home config to speak of, and the
    provider is the one thing they cannot do without. The variable is how they
    say where it is — the same shape as ROBOVIEWER_REPO and ROBOVIEWER_OUTPUT,
    rather than a second `--config`-looking flag to confuse with the first.
    """
    named = os.environ.get(PROVIDER_CONFIG_ENV)
    if named:
        return Path(named).expanduser()
    return Path.home() / ".config" / "roboviewer" / "provider.toml"


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
    """The settings file, the provider file, or the defaults when there is neither.

    An absent home file is normal — the defaults are a working configuration
    apart from the provider. An absent `--config` is a typo: somebody named a
    file, and quietly running on something else is the wrong kindness.
    """
    path = _settings_path(explicit)
    raw = _read(path) if path is not None else {}

    provider_raw = raw.pop("provider", None)
    if provider_raw is not None and explicit is not None:
        # A file in the old shape also has a [provider] section, and there the
        # useful answer is where every key went — not that one section sits in
        # the wrong file. Validate the whole thing first: if it is merely old,
        # that raises with the full list, and the reader gets all of it at once.
        _validate({**raw, "provider": provider_raw}, path)
        raise ValueError(
            f"{path} carries a [provider] section.\n"
            f"  The provider lives in {provider_config_path()} and is read from there "
            f"on every run.\n"
            f"  A file passed with --config holds [reviewer], [judge] and [run] only, "
            f"so it can be copied into an experiment or a write-up without carrying "
            f"a key with it."
        )

    cfg = _validate(raw, path)
    cfg.source = str(path) if path is not None else None
    _attach_provider(cfg, provider_raw, path)
    return cfg


def _settings_path(explicit: Path | None) -> Path | None:
    if explicit is None:
        home = home_config_path()
        return home if home.is_file() else None
    path = explicit.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Config not found: {path}")
    return path


def _read(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _validate(raw: dict[str, Any], path: Path | None) -> Config:
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        where = f"{path}: " if path is not None else ""
        raise ValueError(f"{where}{exc}{_moved_hint(exc)}") from exc


def _attach_provider(cfg: Config, legacy: dict[str, Any] | None, path: Path | None) -> None:
    """The provider comes from its own file; the combined file is the fallback.

    Both present is not an error but it is worth saying out loud: the section
    left behind in the old file is doing nothing, and silence there is how a run
    ends up on an endpoint nobody expected.
    """
    own = provider_config_path()
    if own.is_file():
        cfg.provider = _validate({"provider": _read(own)}, own).provider
        cfg.provider_source = str(own)
        if legacy is not None:
            cfg.provider_notice = (
                f"[provider] in {path} is ignored — the provider comes from {own}. "
                f"Delete the section to stop the two from disagreeing."
            )
        return

    if legacy is not None:
        cfg.provider = _validate({"provider": legacy}, path).provider
        cfg.provider_source = str(path)
        cfg.provider_notice = (
            f"The provider still lives in {path}, together with settings that get "
            f"copied around. Move the [provider] section to {own} — a file with "
            f"nothing else in it has no reason to be copied, and cannot take a key "
            f"along when it is."
        )
