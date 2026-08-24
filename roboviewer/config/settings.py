"""The sections of the two config files, as pydantic models.

    provider.toml   [provider] — how to reach the gateway, set once per machine
    config.toml     [reviewer], [judge], [run] — what to ask of a model and how
                    a run behaves; turned constantly and copied into experiments

Every model forbids unknown keys: a key nobody reads is a setting nobody
applied, and ignoring it silently — the pydantic default — means a typo leaves
the run on defaults while the person who wrote it believes otherwise.

How the files are found and read is `loading`; where the overridable file sets
(checklists, prompts, templates) come from is `overrides`.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Set on every model rather than once at the top: a stray key inside [provider]
# is checked by ProviderConfig, not by the outer one.
STRICT = ConfigDict(extra="forbid")

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
    """How to reach the gateway. What to ask of it is `ModelConfig`.

    A description, not a client: how these fields become request headers and a
    `tool_choice` value is `provider.request`, next to the code that sends them.
    """

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
    # want something else. See provider.example.toml for the combinations.
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"

    # How hard the agent is pushed to submit its result on the final turn.
    # Lower it only when `--check-provider` says the gateway rejects the
    # stronger mode — it prints the value to use.
    terminal_tool_choice: Literal["forced", "required", "auto"] = "forced"

    def api_key_origin(self) -> str:
        """Where the key comes from, as words.

        The origin is the whole of what gets printed. Nothing derived from the
        key itself is — not the ends of it, not a digest, not its length: a
        terminal keeps scrollback, a screenshot outlives the terminal, and a CI
        job keeps its log. "Which file or variable did this come from" is the
        question a 401 actually needs answered.
        """
        if self.api_key:
            return "provider.api_key from the config"
        if os.environ.get(self.api_key_env):
            return f"environment variable {self.api_key_env}"
        return "not found"

    def lookup_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env) or None

    def resolve_api_key(self) -> str:
        key = self.lookup_api_key()
        if not key:
            raise RuntimeError(
                f"No API key found. Set the {self.api_key_env} environment variable "
                f"or provider.api_key in the config."
            )
        return key


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
    # A format is a module in `reports.renders` — see known() there — or a bare
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
