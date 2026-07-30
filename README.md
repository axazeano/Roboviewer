# Roboviewer

Automated code review for merge requests, running entirely on your machine.

Point it at two branches and it comes back with a ranked list of problems worth
fixing — not a wall of style nitpicks. Works with any OpenAI-compatible endpoint,
including a corporate gateway, so your code never has to leave the network you
already trust.

```bash
roboviewer develop
```

```
▸ feature/discount → develop: 12 файлов, 8 пунктов проверки
✓ Корректность и логические ошибки: 3 замечания
✓ Обработка ошибок: 1 замечание
...
⚖ Подтверждено 4 из 11

  F001  [Блокер]      src/cart.py:42  — гонка при параллельном обновлении корзины
  F002  [Существенно] src/api.py:118  — изменение ломает старых клиентов

Отчёт: .roboviewer/runs/20260730-172900/report.md
```

> Prompts, checklist items and generated reports are in Russian. To change that,
> edit `roboviewer/prompts.py` and the files under `roboviewer/checklists/`.

## Install

```bash
git clone git@github.com:axazeano/Roboviewer.git && cd Roboviewer
python3 -m venv .venv && .venv/bin/pip install -e .
ln -sf "$PWD/.venv/bin/roboviewer" ~/.local/bin/roboviewer
```

## Configure

```bash
mkdir -p ~/.config/roboviewer
cp config.example.toml ~/.config/roboviewer/config.toml
export ROBOVIEWER_API_KEY=...
```

Set `provider.base_url` and `provider.model`. Everything else has working
defaults and is documented inline in [config.example.toml](config.example.toml).

Then check the gateway actually works:

```bash
roboviewer --check-provider
```

It makes a handful of targeted requests and names what is wrong — wrong auth
scheme, a `base_url` missing `/v1`, a gateway that cannot do tool calling —
instead of leaving you to infer it from eight agents failing at once.

## Use

```bash
roboviewer <target> [source]
```

The target branch is required. The source defaults to your current branch, and
naming it explicitly lets you review someone else's branch without checking it
out.

```bash
roboviewer develop                    # current branch into develop
roboviewer develop feature/login      # someone else's branch
roboviewer -C ~/projects/app develop  # a repository living elsewhere
```

Set `ROBOVIEWER_REPO` and `ROBOVIEWER_OUTPUT` once and you can drop `-C`/`-o`.
`--diff-only` shows what would be reviewed without spending tokens,
`--only correctness,tests` narrows the checklist, `--no-tui` is for CI.
`--help` has the rest.

## Why the findings are worth reading

Three decisions do most of the work:

**Whole files, not hunks.** The agent gets each changed file in full, with changed
lines marked up. A diff with a few lines of context is the main reason automated
reviewers claim "there is no error handling here" when the guard sits twenty
lines above.

**One agent per concern.** Eight specialised reviewers run in parallel — one for
correctness, one for concurrency, one for tests — rather than one generalist
holding everything at once. Each gets read-only tools to dig through the rest of
the repository on its own.

**A judge at the end.** Every finding is re-checked against the code by a final
agent that discards false positives and downgrades inflated severities. Rejecting
a third of them is a normal outcome.

Comparison is against the **merge base**, so commits that landed on the target
branch after you forked stay out of the review.

## Customise the checklist

One markdown file per concern, in `roboviewer/checklists/default/`:

```markdown
---
id: correctness
title: Корректность и логические ошибки
order: 10
---
Задача для агента...
```

Adding a check means adding a file — no code involved. A `checklists/` directory
inside the repository being reviewed overrides the built-in set.

## Output

`.roboviewer/runs/<timestamp>/` holds `report.md` for humans, plus
`findings.json` and per-item raw results for tuning prompts. `latest` symlinks to
the most recent run. Point `-o` somewhere outside the repository to keep it out
of `git status`.

## License

MIT — see [LICENSE](LICENSE).
