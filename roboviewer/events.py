"""What a running review reports about itself.

A vocabulary shared by everything that has progress to report and by whoever
prints it. It lives apart from `pipeline` so that the stages a review is made of
can emit without importing the thing that runs them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal[
    "run_start", "item_start", "item_progress", "item_done",
    "merge_done", "judge_start", "judge_done", "run_done", "error",
]


@dataclass
class Event:
    kind: EventKind
    message: str = ""
    item_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


def noop(_: Event) -> None:
    """A sink for callers with nothing to show — the tests, mostly."""
