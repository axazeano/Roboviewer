# Review a branch I have not checked out

Someone wants to look at a colleague's branch without switching to it, or at a
repository that is not the one they are standing in — a mirror, a clone that
lives nowhere but this laptop, a second checkout.

## What serves it

| Surface | Part |
| --- | --- |
| `source` (positional) | naming it explicitly instead of taking the current branch |
| `-C, --repo` | a repository somewhere else |

## What it costs to learn

Nothing beyond case 3. The same two positionals, used with both filled in
rather than one. `roboviewer develop feature/login` is the whole feature, and
`-C ~/work/app` is the whole of the other half.

This is where the tool differs from a hosted reviewer in a way people notice:
there is no integration to install and no merge request to exist first. Both
follow from reading two branches through plain git, so the cost of the case is
zero — it is what the implementation already does.

## Verdict: keep

Not separable from case 3 as surface — it is the same two arguments — but worth
its own entry because it is a distinct thing people come for, and because
losing it would mean assuming the branch under review is always checked out.
The CI case depends on the opposite assumption too.
