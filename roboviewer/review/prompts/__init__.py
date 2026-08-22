"""Everything the model reads, in one place.

The eight texts in `default/` are the prompts proper and get rewritten while
tuning a model. Around them, in code, is what the tool assembles: the context
block built from the change under review (`context`), the findings a judge is
shown (`findings`), the output-language directive (`language`), what the model
is told about its tools (`tool_schemas`) and what the runner says to it between
turns (`turns`). `assembly` is the loader and the builders that put the pieces
together.

A custom set in `.roboviewer/prompts/` inside the reviewed repository, or in
the directory `run.prompts_dir` names, overrides the texts file by file.
"""

from __future__ import annotations

from .assembly import DEFAULT_DIR, NAMES, PromptError, Prompts
from .context import ANNOTATION_LEGEND, context_block, references_block
from .language import language_name

__all__ = [
    "ANNOTATION_LEGEND",
    "DEFAULT_DIR",
    "NAMES",
    "PromptError",
    "Prompts",
    "context_block",
    "language_name",
    "references_block",
]
