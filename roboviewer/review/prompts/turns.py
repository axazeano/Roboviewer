"""What the runner says to an agent between the model's turns.

Protocol rather than review wording — a turn budget, a warning that it is
nearly spent, a nudge when a reply called no tool — but text the model reads all
the same, so it is written here with the rest of the prompt surface and handed
to the runner on every `AgentRequest`. The runner fills the placeholders: the
turn limit, the name of the tool that ends the run, the turns left.
"""

from __future__ import annotations

from ...provider import TurnNotes

TURN_NOTES = TurnNotes(
    # Appended to the system prompt.
    budget="""

# Turn budget

You have {max_turns} turns. A turn is one reply from you, with or without tool
calls. Plan for that: investigate what matters most first, and call `{terminal}`
while turns remain. A review that is never submitted is a review nobody reads.
""",
    # Said after the tool results when the limit is close, so it is the last
    # thing the agent reads before deciding what to do with the turn it has left.
    wrap_up=(
        "{left} turn(s) left. Do not start anything new — finish the check you are "
        "on and call `{terminal}` now with what you already have."
    ),
    # Said after a reply that called no tool and was not a submission.
    nudge=(
        "Keep going. {left} turn(s) left, and you must call the {terminal} tool "
        "before they run out."
    ),
)
