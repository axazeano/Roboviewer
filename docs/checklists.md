# Customise the checklist

One markdown file per concern, in `roboviewer/checklists/default/`:

```markdown
---
id: correctness
title: Correctness and logic errors
order: 10
---
The task for the agent...
```

Adding a check means adding a file — no code involved. A `checklists/` directory
inside the repository being reviewed overrides the built-in set. An optional
`_system.md` in the directory replaces the system prompt for its items.

Three checklist sets ship with the tool, and how many agents a set spreads the
aspects over is a trade worth measuring — see [Tuning](tuning.md).
