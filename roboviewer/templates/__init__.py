"""Report templates, loaded from files.

Same arrangement as `prompts`: the bundled set lives in `default/`, a user
directory is searched first, and resolution is per file — a custom set carries
only the templates it actually changes and picks up improvements to the rest.

Templates are named `<document>.<target format>.j2`, partials start with an
underscore, and the format extension is not decoration: it decides escaping.
Markdown is emitted verbatim, HTML has every value escaped, because a finding's
title is model output quoted back and a report is a file people open in a
browser.

Markdown and HTML each get their own macros rather than sharing an abstracted
one. What they share is the view model; trying to abstract markup across output
formats is where template systems go to die.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined
from jinja2 import TemplateError as _JinjaTemplateError
from jinja2 import TemplateNotFound
from markdown_it import MarkdownIt
from markupsafe import Markup
from pydantic import BaseModel

from ..models import SEVERITY_LABEL_RU, Severity

DEFAULT_DIR = Path(__file__).resolve().parent / "default"

# Presentation, so it lives next to the templates rather than in the models. The
# same two tables serve markdown and HTML, which is why they are not inlined in
# either template.
SEVERITY_ICON: dict[Severity, str] = {
    Severity.BLOCKER: "🛑",
    Severity.MAJOR: "⚠️",
    Severity.MINOR: "🔹",
    Severity.NIT: "💬",
}

STATUS_ICON: dict[str, str] = {
    "ok": "✅",
    "failed": "❌",
    "skipped": "⏭",
    "pending": "…",
    "running": "…",
}

# `html: False` is the point of this line. Rationale and suggestion are model
# output, and a report is opened in a browser: the only markup allowed through
# is what the parser itself produced, never what the text asked for.
# `breaks` matches how GitLab renders a comment, so the same text reads the same
# way in the report and in the merge request.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": True})


class TemplateError(RuntimeError):
    pass


def _thousands(value: int) -> str:
    """12345 → "12 345". A thin space would read better but breaks copy-paste
    out of a terminal, so it stays an ordinary one."""
    return f"{value:,}".replace(",", " ")


def _percent(value: float) -> str:
    return f"{value:.0%}"


def _fixed(value: float, digits: int = 0) -> str:
    return f"{value:.{digits}f}"


def _blockquote(text: str, marker: str = "> ") -> str:
    return marker + text.replace("\n", "\n" + marker)


def _markdown(text: str) -> Markup:
    """Prose written as markdown, turned into HTML. Marked safe because the
    parser produced it — see `_MARKDOWN` for why that is not the same as
    trusting the text."""
    return Markup(_MARKDOWN.render(text))


def _autoescape(name: str | None) -> bool:
    """Escaping follows the target format, which is the extension before `.j2`."""
    if name is None:
        return False
    return name.removesuffix(".j2").endswith((".html", ".htm", ".xml"))


@lru_cache(maxsize=8)
def environment(directory: Path | None = None) -> Environment:
    """Cached per directory: a comparison document renders one template per run
    and has no reason to rebuild the environment each time. Jinja still stats
    template files on its own, so edits are picked up between renders."""
    loaders: list[FileSystemLoader] = []
    if directory is not None:
        loaders.append(FileSystemLoader(str(directory)))
    loaders.append(FileSystemLoader(str(DEFAULT_DIR)))

    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=_autoescape,
        # Block tags sit on their own lines and must not leave blank ones behind:
        # in markdown an extra newline is a paragraph break, not whitespace.
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        # A typo in a field name has to fail loudly. Silently rendering an empty
        # string would ship a report with a section quietly missing.
        undefined=StrictUndefined,
    )
    env.filters["thousands"] = _thousands
    env.filters["percent"] = _percent
    env.filters["fixed"] = _fixed
    env.filters["blockquote"] = _blockquote
    env.filters["markdown"] = _markdown
    env.globals["SEVERITY_LABEL"] = SEVERITY_LABEL_RU
    env.globals["SEVERITY_ICON"] = SEVERITY_ICON
    env.globals["STATUS_ICON"] = STATUS_ICON
    return env


def render(name: str, context: BaseModel, directory: Path | None = None) -> str:
    """Renders a context model with the named template. `directory` overrides
    bundled templates file by file.

    The context is any model, not the review view specifically: a comparison of
    several runs has a different shape, and squeezing it through the per-run
    view to reuse this function would be violence.
    """
    env = environment(directory)
    try:
        template = env.get_template(name)
    except TemplateNotFound as exc:
        searched = f"{directory}, " if directory is not None else ""
        raise TemplateError(
            f"Шаблон {name} не найден: искали в {searched}{DEFAULT_DIR}"
        ) from exc

    # Field by field rather than as a dump: templates keep attribute access and
    # enums stay enums, so SEVERITY_ICON[f.severity] works.
    values = {field: getattr(context, field) for field in type(context).model_fields}
    try:
        return template.render(values)
    except _JinjaTemplateError as exc:
        raise TemplateError(f"Шаблон {name} не отрендерился: {exc}") from exc
