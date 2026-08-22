"""Finding and reading the two config files.

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
from typing import Any

from pydantic import ValidationError

from .settings import MOVED, Config

PROVIDER_CONFIG_ENV = "ROBOVIEWER_PROVIDER_CONFIG"


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


def home_config_path() -> Path:
    return Path.home() / ".config" / "roboviewer" / "config.toml"


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
