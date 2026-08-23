"""Settings: the sections of the two config files, how they are found and read,
and where the overridable file sets come from.

`settings` is the shape, `loading` the files, `overrides` the directories. The
names below are what the rest of the tool imports.
"""

from __future__ import annotations

from .loading import PROVIDER_CONFIG_ENV, home_config_path, load_config, provider_config_path
from .settings import (
    DEFAULT_EXCLUDES,
    MOVED,
    STRICT,
    Config,
    ModelConfig,
    ProviderConfig,
    RateLimits,
    RunConfig,
)

__all__ = [
    "DEFAULT_EXCLUDES",
    "MOVED",
    "PROVIDER_CONFIG_ENV",
    "STRICT",
    "Config",
    "ModelConfig",
    "ProviderConfig",
    "RateLimits",
    "RunConfig",
    "home_config_path",
    "load_config",
    "provider_config_path",
]
