"""Configuration loading.

Layers are stacked from general to specific:

    built-in defaults
    → ~/.config/roboviewer/config.toml     (provider, shared across repositories)
    → <repo>/.roboviewer/config.toml       (per-project specifics)
    → --config PATH                         (explicitly given file)
    → CLI flags

Stacking, not "first match wins": otherwise a repo config holding nothing but a
[run] section would wipe out the provider from the home config and the run would
silently go to the wrong endpoint.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

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


class ProviderConfig(BaseModel):
    """OpenAI-compatible provider. base_url points at the custom gateway."""

    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "ROBOVIEWER_API_KEY"
    # Key inlined in the config: takes precedence over the environment variable.
    # Handy while debugging, but such a file must never reach git.
    api_key: str | None = None
    model: str = "gpt-4o"
    judge_model: str | None = None  # None → same as model
    temperature: float = 0.1
    max_tokens: int = 8000
    timeout_s: float = 300.0
    max_retries: int = 3
    # Some gateways cannot handle several tool_calls in one response.
    parallel_tool_calls: bool = True
    extra_headers: dict[str, str] = Field(default_factory=dict)

    # How to pass the key. Defaults to the OpenAI way: Authorization: Bearer <key>.
    # Gateways often want something else:
    #   auth_header = "api-key",   auth_scheme = ""        → api-key: <key>   (Azure)
    #   auth_header = "X-Api-Key", auth_scheme = ""        → X-Api-Key: <key>
    #   auth_scheme = "Token"                              → Authorization: Token <key>
    # When auth_header is not Authorization, the built-in Bearer header is dropped:
    # a stray Authorization header alone makes some gateways answer 401.
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"

    # How the agent is pushed to submit its result on the final turn.
    #   "forced"   — tool_choice = {"type": "function", ...}, names the exact tool
    #   "required" — tool_choice = "required", any tool but at least one
    #   "auto"     — no pressure at all; relies on the plain-text JSON fallback
    # Lower the setting only when `--check-provider` shows the gateway rejects the
    # stronger mode.
    terminal_tool_choice: Literal["forced", "required", "auto"] = "forced"

    def api_key_source(self) -> tuple[str | None, str]:
        """(key, where it came from) — the source is what matters when debugging 401."""
        if self.api_key:
            return self.api_key, "provider.api_key из конфига"
        from_env = os.environ.get(self.api_key_env)
        if from_env:
            return from_env, f"переменная окружения {self.api_key_env}"
        return None, "не найден"

    def resolve_api_key(self) -> str:
        key, _ = self.api_key_source()
        if not key:
            raise RuntimeError(
                f"API-ключ не найден. Задай переменную окружения {self.api_key_env} "
                f"или provider.api_key в конфиге."
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
        return f"{key[:4]}…{key[-4:]} ({len(key)} симв.)" if len(key) > 12 else f"(короткий, {len(key)} симв.)"

    def resolve_judge_model(self) -> str:
        return self.judge_model or self.model

    def terminal_tool_choice_value(self, tool_name: str) -> Any:
        if self.terminal_tool_choice == "forced":
            return {"type": "function", "function": {"name": tool_name}}
        return self.terminal_tool_choice  # "required" / "auto"


class RunConfig(BaseModel):
    # Branches are deliberately absent: the target is always a CLI argument and
    # the source defaults to the current branch.
    checklist_dir: str = "checklists/default"
    output_dir: str = ".roboviewer/runs"
    # Checklist items reviewed at the same time — concurrent requests to the model,
    # not OS threads.
    concurrency: int = 4
    max_turns: int = 25
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
    # Drop findings below this confidence before the judge even sees them.
    min_confidence: float = 0.0
    enable_judge: bool = True
    # Maximum lines returned by a single read_file call.
    max_read_lines: int = 800


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    # Which files took part, in stacking order.
    sources: list[str] = Field(default_factory=list)


def home_config_path() -> Path:
    return Path.home() / ".config" / "roboviewer" / "config.toml"


def repo_config_path(repo_root: Path) -> Path:
    return repo_root / ".roboviewer" / "config.toml"


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(repo_root: Path, explicit: Path | None = None) -> Config:
    layers: list[Path] = [p for p in (home_config_path(), repo_config_path(repo_root)) if p.is_file()]

    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Конфиг не найден: {path}")
        layers.append(path)

    raw: dict = {}
    for path in layers:
        with path.open("rb") as fh:
            raw = _deep_merge(raw, tomllib.load(fh))

    cfg = Config.model_validate(raw)
    cfg.sources = [str(p) for p in layers]
    return cfg
