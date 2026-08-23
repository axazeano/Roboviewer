"""Which reports a run writes: project setting versus flag.

The list lives in the config because for a project it is a standing decision.
The flag exists to step away from it for one run, and therefore replaces the
list entirely — otherwise "just markdown today" cannot be said at all.
"""

from __future__ import annotations

import argparse

import pytest

from roboviewer.cli.arguments import apply_overrides, build_parser, report_formats
from roboviewer.config import Config


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["develop", *argv])


def test_default_is_markdown_only() -> None:
    assert Config().run.report_formats == ["md"]


def test_format_is_split_on_commas() -> None:
    assert report_formats("md,html") == ["md", "html"]


def test_spaces_around_commas_are_tolerated() -> None:
    assert report_formats(" md , html ") == ["md", "html"]


def test_empty_format_is_rejected_rather_than_writing_nothing() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        report_formats(" , ")


def test_config_value_survives_when_the_flag_is_absent() -> None:
    cfg = Config()
    cfg.run.report_formats = ["html"]

    assert apply_overrides(cfg, parse()).run.report_formats == ["html"]


def test_flag_replaces_the_configured_list_entirely() -> None:
    cfg = Config()
    cfg.run.report_formats = ["md", "html"]

    result = apply_overrides(cfg, parse("--format", "md"))

    assert result.run.report_formats == ["md"]
