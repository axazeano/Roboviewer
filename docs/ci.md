# Continuous integration

There is nothing to install on your forge. The run writes files, the pipeline
publishes them, and the one thing a runner reads back is the exit code.

| Code | Means |
| --- | --- |
| `0` | Nothing at or above `--fail-on` |
| `1` | Confirmed findings at or above `--fail-on` — the branch has work to do |
| `2` | The tool could not run: bad config, no branch point, nowhere to write |
| `3` | The review ran, but a checklist item failed and its aspect went unreviewed |

`--fail-on` takes `blocker`, `major`, `minor`, `nit` or `never`, and defaults to
`never`: reporting is the job, failing the build is opt-in. Only confirmed
findings inside the changed lines count — one the judge threw out, or one
pointing at code the branch never touched, must never turn a pipeline red. Codes
`1` and `3` are separate because one is fixed in the branch and the other rerun.

In a merge-request pipeline the target branch comes from the environment —
`CI_MERGE_REQUEST_TARGET_BRANCH_NAME` or `GITHUB_BASE_REF` — so neither `--into`
nor `--from` is needed, and the source is whatever the runner checked out.
Both runners clone shallow by default, and the branch point is usually missing
from such a clone: give the job full history, or the run stops on code `2`
saying so.

A runner has no `~/.config/roboviewer/`, so the pipeline has to say where both
files are.

Commit the settings into the repository — `.roboviewer/config.toml`, next to the
run output, is the obvious place — and name it with `--config`. Then the
pipeline and everyone's laptop read the same file. Nothing is picked up
implicitly: a file inside the repository under review is read when the command
line names it and not otherwise.

The provider does not go in that file, and a `[provider]` section there is
refused — which is the point, since that file is committed. Commit a
`provider.toml` holding the address and `api_key_env` only, point
`ROBOVIEWER_PROVIDER_CONFIG` at it, and let the key arrive as
`ROBOVIEWER_API_KEY` from the masked variable.

## GitLab

```yaml
# .gitlab-ci.yml
review:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_DEPTH: 0                       # the branch point has to exist locally
    ROBOVIEWER_API_KEY: $LLM_API_KEY
    ROBOVIEWER_PROVIDER_CONFIG: .roboviewer/provider.toml
  script:
    - pip install -q git+https://github.com/axazeano/Roboviewer.git
    - roboviewer review --config .roboviewer/config.toml --format md,codequality --fail-on blocker
  after_script:                        # runs even when the gate failed the job
    - cp .roboviewer/runs/latest/{gl-code-quality-report.json,report.md} .
  artifacts:
    when: always
    paths: [report.md]
    reports:
      codequality: gl-code-quality-report.json
```

That puts every finding in the merge request widget and on the diff itself,
without anything of yours leaving the runner.

## GitHub

```yaml
# .github/workflows/review.yml
on: pull_request
permissions:
  contents: read
  security-events: write               # for the SARIF upload
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - run: pip install -q git+https://github.com/axazeano/Roboviewer.git
      - run: roboviewer review --config .roboviewer/config.toml --format md,sarif --fail-on blocker
        env:
          ROBOVIEWER_PROVIDER_CONFIG: .roboviewer/provider.toml
        env:
          ROBOVIEWER_API_KEY: ${{ secrets.LLM_API_KEY }}
      - if: always()
        run: cp .roboviewer/runs/latest/report.sarif report.sarif
      - if: always()
        uses: github/codeql-action/upload-sarif@v3
        with: { sarif_file: report.sarif }
```

Findings arrive as Code Scanning alerts, matched across runs by fingerprint, so
a finding you fixed closes itself. Both jobs copy the files out of
`runs/latest/` rather than pointing at the symlink: what an uploader does with a
symlink differs between runners, and a copy behaves the same everywhere.
