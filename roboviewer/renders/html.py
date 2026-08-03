"""Отчёт в HTML — один самодостаточный файл.

Стили инлайном, ни ссылок, ни скриптов, ни внешних картинок: такой отчёт
открывают двойным щелчком и цепляют к тикету, тянуть ему неоткуда.
"""

from __future__ import annotations

from pathlib import Path

from ..models import ReviewRun
from ..view import build_view
from ._jinja import render_template

NAME = "html"
FILENAME = "report.html"
TEMPLATE = "report.html.j2"


def render(run: ReviewRun, templates_dir: Path | None = None) -> str:
    return render_template(TEMPLATE, build_view(run), templates_dir)
