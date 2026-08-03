"""Отчёт в markdown — то, что читают в терминале и вставляют в MR."""

from __future__ import annotations

from pathlib import Path

from ..models import ReviewRun
from ..view import build_view
from ._jinja import render_template

NAME = "md"
FILENAME = "report.md"
TEMPLATE = "report.md.j2"


def render(run: ReviewRun, templates_dir: Path | None = None) -> str:
    return render_template(TEMPLATE, build_view(run), templates_dir)
