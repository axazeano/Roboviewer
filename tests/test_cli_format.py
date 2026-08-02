"""Какие отчёты пишет прогон: настройка проекта против флага.

Список живёт в конфиге, потому что для проекта это постоянное решение. Флаг
существует, чтобы отступить от него на один прогон, и потому заменяет список
целиком — иначе «сегодня только markdown» сказать нечем.
"""

from __future__ import annotations

import argparse

import pytest

from roboviewer.cli import _apply_overrides, build_parser, report_templates
from roboviewer.config import Config


def parse(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(["develop", *argv])


def test_default_is_markdown_only() -> None:
    assert Config().run.report_templates == ["report.md.j2"]


def test_format_maps_to_template_names() -> None:
    assert report_templates("md,html") == ["report.md.j2", "report.html.j2"]


def test_spaces_around_commas_are_tolerated() -> None:
    assert report_templates(" md , html ") == ["report.md.j2", "report.html.j2"]


def test_empty_format_is_rejected_rather_than_writing_nothing() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        report_templates(" , ")


def test_config_value_survives_when_the_flag_is_absent() -> None:
    cfg = Config()
    cfg.run.report_templates = ["report.html.j2"]

    assert _apply_overrides(cfg, parse()).run.report_templates == ["report.html.j2"]


def test_flag_replaces_the_configured_list_entirely() -> None:
    cfg = Config()
    cfg.run.report_templates = ["report.md.j2", "report.html.j2"]

    result = _apply_overrides(cfg, parse("--format", "md"))

    assert result.run.report_templates == ["report.md.j2"]
