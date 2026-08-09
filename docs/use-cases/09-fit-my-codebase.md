# Fit the review to my codebase

The bundled checks are stack-agnostic. Someone with a real repository wants the
review to look at what matters there, ignore what does not, and stop reporting
on code the branch never touched.

## What serves it

| Surface | Part |
| --- | --- |
| `--checklist DIR`, `run.checklist_dir` | which set of checks runs |
| `--only a,b` | just these items, this once |
| `run.prompts_dir`, `run.templates_dir` | replacing the bundled texts |
| `run.exclude_globs` | files kept out entirely |
| `run.enforce_scope`, `run.scope_margin` | keep findings near the changed lines |
| `run.resolve_references` | the reference pre-pass |
| `run.inline_max_lines`, `run.inline_max_total_chars` | how much of a file goes in whole |
| `run.diff_context_lines`, `run.diff_max_chars` | how much goes in as hunks |
| `run.max_read_lines` | how much one `read_file` returns |

## What it costs to learn

Adding a check is one markdown file with three frontmatter lines and no code,
and a `checklists/` directory in the repository overrides the bundled set. That
part is as cheap as it can be.

The cost is in the other nine settings, and it is not their count — it is that
three separate ideas are wearing the same clothes.

**Five numbers for one question.** `inline_max_lines`,
`inline_max_total_chars`, `diff_context_lines`, `diff_max_chars` and
`max_read_lines` all answer "how much code does the model get to see", and
nothing says how they interact. A person whose run is too expensive has five
numbers and no order to try them in. The measured reality is that one of them —
`inline_max_total_chars` at 400000 — is what a single 539 KB `project.pbxproj`
would swallow whole, which is the kind of thing that should be discoverable
from the report rather than from arithmetic.

**Two settings for one idea.** `enforce_scope` and `scope_margin` are on/off
and how-far for the same rule, and off is the same as a margin of infinity.

**One directory, two rules.** `prompts_dir` and `templates_dir` fall back to
`.roboviewer/prompts` and `.roboviewer/templates` inside the reviewed
repository **just by existing** — nothing names them — while the config file
sitting next to them is read only where you point it. Same directory, opposite
rules, and the implicit half is the one that changes a run's behaviour without
appearing in the command that produced it.

## Verdict: fold

The use case stays; it is a large part of why the tool is worth running twice.
What should not stay is five numbers presenting themselves as five decisions,
and a directory that changes behaviour by existing.

Three drafts filed:

- fold the context budget into something a person can reason about, and report
  which limit actually bit on a given run;
- make the scope margin carry its own off state instead of pairing with a
  boolean;
- require the prompt and template directories to be named rather than
  discovered, matching what the config file now does.
