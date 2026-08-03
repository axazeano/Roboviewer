"""Какие отчёты пишет прогон: настройка проекта против флага.

Список живёт в конфиге, потому что для проекта это постоянное решение. Флаг
существует, чтобы отступить от него на один прогон, и потому заменяет список
целиком — иначе «сегодня только markdown» сказать нечем.
"""

from __future__ import annotations

import argparse

import pytest

from roboviewer.cli import _apply_overrides, build_parser, report_formats
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

    assert _apply_overrides(cfg, parse()).run.report_formats == ["html"]


def test_flag_replaces_the_configured_list_entirely() -> None:
    cfg = Config()
    cfg.run.report_formats = ["md", "html"]

    result = _apply_overrides(cfg, parse("--format", "md"))

    assert result.run.report_formats == ["md"]
