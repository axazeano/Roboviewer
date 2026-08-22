"""The review itself: what is asked of the agents, and what is done with their answers.

`pipeline` is the order things happen in — one agent per checklist item, then
merge, then the scope gate, then the judge. Everything it calls is a module
beside it: `checklist` reads the items, `prompts` assembles what the model is
shown, `submissions` turns what the agents hand back into models, `merge`
collapses one defect written up twice, `scope` keeps findings to the change,
`judge` decides what survives.

Below this package sit `repo` (the code), `provider` (the model) and the shared
vocabulary in `models`; above it, `reports` render the run and `cli` drives it.
"""

from __future__ import annotations

from .checklist import ChecklistItem, load_checklist
from .pipeline import ReviewPipeline, output_dir_for
from .prompts import PromptError, Prompts

__all__ = [
    "ChecklistItem",
    "PromptError",
    "Prompts",
    "ReviewPipeline",
    "load_checklist",
    "output_dir_for",
]
