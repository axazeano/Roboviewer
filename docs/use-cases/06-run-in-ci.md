# Run inside a pipeline and fail the build

Someone wants every merge request reviewed automatically, the findings visible
where the team already looks, and the job to go red when something serious is
left standing.

## What serves it

| Surface | Part |
| --- | --- |
| `--fail-on SEVERITY`, `run.fail_on` | the severity at which the run exits 1 |
| exit codes 0/1/2/3 | the whole of what a runner reads back |
| `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `GITHUB_BASE_REF` | the target branch, so the command line carries none |
| `--config PATH` | a runner has no home config, so the pipeline names one |
| `--format sarif`, `--format codequality` | formats the forge already understands |

## What it costs to learn

One flag and a table of four exit codes. The target branch is taken from the
pipeline's own variable, so the job does not repeat a branch name that is
already there and cannot get it wrong.

Two things are deliberately not knobs, and both save a person from a decision:
only confirmed findings inside the changed lines can trip the gate, and the
default is `never`, so adding the tool to a pipeline cannot break it on the
first day. Reporting is the job; failing the build is opt-in.

The one thing that has to be learned from outside is the shallow clone. Both
runners clone shallow by default and the branch point is usually missing, which
surfaces as a git error that reads like something else. The tool now names that
cause and says how to deepen the clone, which is the difference between a
five-minute fix and an afternoon.

## Verdict: keep

The exit codes are four rather than two because "the review found a blocker"
and "an agent crashed" call for different reactions — one is fixed in the
branch, the other rerun. That is one idea, not four, and a runner reads it as a
number either way.
