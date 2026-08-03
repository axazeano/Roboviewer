"""Рендеры отчёта — по файлу на формат.

Рендер отвечает на три вопроса: как он называется в `--format`, как называется
файл, который он пишет, и как из прогона получается его содержимое. Всё, что
делает файл рядом с этим, — модуль с `NAME`, `FILENAME` и `render(run,
templates_dir)`; список каталога и есть список форматов.

Шаблонными они быть не обязаны. SARIF или Code Quality для GitLab — это
сериализация, а не документ, и генерировать JSON текстовым шаблоном означает
однажды получить невалидный файл из-за кавычки в тексте модели. Такой рендер
ляжет сюда же обычным модулем и просто не тронет Jinja.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models import ReviewRun
from ..view import build_view
from . import html, markdown
from ._errors import RenderError, TemplateError
from ._jinja import (
    DEFAULT_DIR,
    compile_template,
    environment,
    render_template,
    template_exists,
)

__all__ = [
    "DEFAULT_DIR",
    "Render",
    "RenderError",
    "TemplateError",
    "environment",
    "known",
    "prepare",
    "render_template",
    "resolve",
]


class Render(Protocol):
    NAME: str
    FILENAME: str

    def render(self, run: ReviewRun, templates_dir: Path | None = None) -> str: ...


# Явный список, а не обход каталога: неизвестный формат должен падать на понятной
# ошибке, а не на импорте случайного модуля.
BUILTIN: tuple[Render, ...] = (markdown, html)
REGISTRY: dict[str, Render] = {render.NAME: render for render in BUILTIN}


@dataclass(frozen=True)
class _CustomTemplate:
    """Формат, которого нет в коде, но под который лежит шаблон в templates_dir.

    Нужен, чтобы свой документ — скажем, короткий комментарий в MR — заводился
    одним файлом, без правки Python.
    """

    NAME: str
    FILENAME: str
    TEMPLATE: str

    def render(self, run: ReviewRun, templates_dir: Path | None = None) -> str:
        return render_template(self.TEMPLATE, build_view(run), templates_dir)


def known() -> list[str]:
    return [render.NAME for render in BUILTIN]


def resolve(name: str, templates_dir: Path | None = None) -> Render:
    if name in REGISTRY:
        return REGISTRY[name]

    template = f"report.{name}.j2"
    if template_exists(template, templates_dir):
        return _CustomTemplate(NAME=name, FILENAME=f"report.{name}", TEMPLATE=template)

    raise RenderError(
        f"Неизвестный формат отчёта: {name}. Известные: {', '.join(known())}. "
        f"Свой заводится шаблоном {template} в каталоге шаблонов."
    )


def prepare(formats: Sequence[str], templates_dir: Path | None = None) -> list[Render]:
    """Резолвит форматы и компилирует их шаблоны, ничего не рендеря.

    Зовётся дважды: на старте прогона, чтобы опечатка в `--format` стоила
    секунду, а не полный счёт за токены, и в `save()` перед первой записью,
    чтобы не оставить на диске половину отчётов.
    """
    chosen = [resolve(fmt, templates_dir) for fmt in formats]
    for render in chosen:
        template = getattr(render, "TEMPLATE", None)
        if template:
            compile_template(template, templates_dir)
    return chosen
