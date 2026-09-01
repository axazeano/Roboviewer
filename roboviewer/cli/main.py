"""The commands: the order the steps run in, and what a failure exits with.

Each step below either produces its part of the run or raises `CLIError`, so
`main` has one place that prints a failure and one that decides the exit code.
Which command is being run is decided once, at the top of `_execute`, and every
command after that is a few steps of the same list.

The commands and their flags are `arguments`, what the steps produce is printed
by `console`, and where their files come from is decided by `config.overrides`.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import ValidationError

from .. import comments, repo
from ..config import Config, load_config, overrides, provider_config_path
from ..models import ReviewRun
from ..observer import SILENT, Broadcast, RunObserver
from ..provider import OpenAIAgentRunner, Runner
from ..repo.diff import change_map
from ..reports import renders, save
from ..review import (
    ChecklistItem,
    PromptError,
    Prompts,
    ReviewPipeline,
    load_checklist,
    output_dir_for,
)
from . import ci_env, console, exit_codes
from .arguments import NEEDS_REPOSITORY, apply_overrides, build_parser


class CLIError(RuntimeError):
    """A failure the user can do something about.

    Every setup step raises this instead of printing and returning a code of its
    own — thirteen of those in one function was most of what made it unreadable.
    """

    def __init__(self, message: str, hint: str = "", code: int = exit_codes.SETUP) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


@dataclass
class RunPlan:
    """Everything the review needs, with every failure already surfaced."""

    cfg: Config
    changes: repo.ChangeSet
    items: list[ChecklistItem]
    prompts: Prompts
    runner: Runner
    templates_dir: Path | None


def main(argv: list[str] | None = None, observer: RunObserver = SILENT) -> int:
    """The command. `observer` is how something outside the tool asks to be
    told what the run and its agents did — see `observer`; by default nobody is
    watching and the run keeps no account of itself."""
    args = build_parser().parse_args(argv)
    try:
        return _execute(args, observer)
    except CLIError as exc:
        console.error(exc.message, exc.hint)
        return exc.code


def _execute(args: argparse.Namespace, observer: RunObserver) -> int:
    """Which command runs, and where each of them stops.

    Everything that fails raises `CLIError`; what stops on purpose returns a code
    of its own, because `diff` finishing is not a failure.
    """
    setting_up = _setup_command(args)
    if setting_up is not None:
        return setting_up

    root = _repo_root(args.repo, required=args.command in NEEDS_REPOSITORY)
    cfg = apply_overrides(_config(args.config), args)
    if args.command == "show-config":
        console.config(cfg, root)
        return 0
    if args.command == "list-items":
        console.checklist_items(_checklist(cfg, root, args.only))
        return 0
    if args.command == "diff":
        console.diff_summary(_changes(cfg, root, _target(args), args.source))
        return 0
    if args.command == "comment":
        return _comment_command(cfg, root, args)
    return _review_command(cfg, root, args, observer)


def _review_command(
    cfg: Config, root: Path, args: argparse.Namespace, observer: RunObserver
) -> int:
    """The checklist is read before git is asked anything: a misspelled item is
    the cheapest failure there is, and it should not wait for a diff."""
    items = _checklist(cfg, root, args.only)
    changes = _changes(cfg, root, _target(args), args.source)
    compared = changes.comparison
    if not changes.files:
        console.notice(f"No changes in {compared.source} relative to {compared.target}.")
        return 0

    plan = _plan(cfg, root, changes, items)
    if compared.detached:
        console.notice(
            f"Reviewing branch {compared.source} ({compared.head_sha[:12]}); "
            "the working copy is untouched."
        )
    return asyncio.run(_review(plan, args.verbose, observer))


def _setup_command(args: argparse.Namespace) -> int | None:
    """The two commands about the tool itself rather than about a repository:
    one writes the configuration, the other probes the gateway with it.

    Returns None when this run is neither.
    """
    if args.command == "init":
        # Before the config is read, not after: the wizard is what somebody runs
        # when there is no config, or when the one there is does not load.
        from .init import run_init

        return run_init()

    if args.command == "check-provider":
        # The one command that reads nothing but the provider file
        from .check_provider import check_provider

        cfg = _config(args.config)
        return check_provider(cfg.provider, cfg.reviewer.model, cfg.provider_source)
    return None


def _comment_command(cfg: Config, root: Path, args: argparse.Namespace) -> int:
    """Post a run that is already on disk, and never run one.

    Composing comes before choosing a forge: it is where a dry run stops, and
    the only step that can fail on the run rather than on the forge.
    """
    directory = _run_directory(cfg, root, args.run)
    run = _saved_run(directory)
    draft = comments.compose(run, _commentable(root, run))

    pull = _pull_request(args)
    if args.dry_run:
        console.would_post(pull, draft, directory)
        return 0

    token = comments.token_for(pull.forge)
    if not token:
        raise CLIError(str(comments.missing_token(pull)), _token_hint(pull))

    console.notice(f"Posting to {pull.name}: {pull.slug}#{pull.number}")
    forge = comments.forge_for(pull, token)
    try:
        result = forge.post(pull, draft)
    except comments.ForgeError as exc:
        raise CLIError(f"Could not post the review: {exc}") from exc
    console.posted(result, draft)
    return 0


def _pull_request(args: argparse.Namespace) -> comments.PullRequest:
    """Which merge request to post to: what the job says, and what the flags say
    over it. Outside a pipeline both flags are the only way to know."""
    found = comments.detect()
    slug = args.project or (found.slug if found else "")
    number = args.pull or (found.number if found else None)
    if not slug or number is None:
        raise CLIError(
            "No pull request to post to.",
            "Inside a merge-request pipeline this comes from the environment. "
            "Outside one, name it: --project owner/name --pull NUMBER.",
        )
    if found is None:
        return comments.on_github(slug, number)
    if slug != found.slug and not args.pull:
        # The number would still be the job's, so this would post to a number
        # that means something else in the repository being named.
        raise CLIError(
            f"--project names {slug}, but the number would come from the job, "
            f"which runs for {found.slug}#{found.number}.",
            "Name the number too: --pull NUMBER.",
        )
    return replace(found, slug=slug, number=number)


def _run_directory(cfg: Config, root: Path, explicit: Path | None) -> Path:
    """The run to post. `latest` is the symlink `save` leaves behind, which is
    what the review step in the same job just wrote."""
    if explicit is not None:
        return explicit.expanduser()
    return output_dir_for(cfg, root, "latest")


def _saved_run(directory: Path) -> ReviewRun:
    path = directory / "run.json"
    try:
        return ReviewRun.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CLIError(
            f"No run to post at {path}.",
            "Point at a run directory with --run PATH, or run `roboviewer review` first.",
        ) from exc
    except ValidationError as exc:
        raise CLIError(f"{path} is not a run this version can read: {exc}") from exc


def _commentable(root: Path, run: ReviewRun) -> dict[str, set[int]]:
    """The lines a forge can hang a comment on: the ones the diff added.

    Read from git, not from the run: a run records which findings were kept,
    not which lines they were kept against.
    """
    try:
        changes = change_map(root, run.base_sha, run.head_sha, [f.file for f in run.files])
    except repo.GitError as exc:
        raise CLIError(
            f"Git error: {exc}",
            (
                f"The run compared {run.base_sha[:12]}..{run.head_sha[:12]}, and this "
                f"clone cannot. {_depth_hint(root)}"
            ).strip(),
        ) from exc
    return {path: entry.added for path, entry in changes.items()}


def _token_hint(pull: comments.PullRequest) -> str:
    """Where the token comes from in the one place that hands a job one."""
    if pull.forge != comments.GITHUB:
        return ""
    return (
        "In GitHub Actions the job is handed one: pass it as "
        "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}, with `pull-requests: write` "
        "in the job's permissions."
    )


def _target(args: argparse.Namespace) -> str:
    target = args.target or _target_from_ci()
    if not target:
        raise CLIError(
            "No target branch given.",
            "Name it with --into <branch>. In a merge-request pipeline it comes "
            "from the environment instead.",
        )
    return target


def _target_from_ci() -> str | None:
    """The target branch a merge-request pipeline already names in a variable.

    Announced rather than assumed: a review is against a branch, and which one
    should be readable in the job log without knowing the runner's variables.
    """
    environment = ci_env.detect()
    if environment is None:
        return None
    console.notice(
        f"Target branch from {environment.name}: "
        f"{environment.target} (${environment.variable})"
    )
    return environment.target


def _repo_root(requested: str, *, required: bool) -> Path:
    path = Path(requested).expanduser().resolve()
    try:
        return repo.repo_root(path)
    except repo.GitError as exc:
        if required:
            raise CLIError(
                f"Error: {exc}",
                "Point at a repository with --repo PATH or the ROBOVIEWER_REPO variable.",
            ) from exc
        # A diagnostic command has no repository to work on and does not need one
        return path


def _config(explicit: Path | None) -> Config:
    try:
        return load_config(explicit)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(f"Config error: {exc}") from exc


def _checklist(cfg: Config, root: Path, only: str | None) -> list[ChecklistItem]:
    try:
        return load_checklist(
            overrides.checklist_dir(cfg, root),
            only.split(",") if only else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(f"Checklist error: {exc}") from exc


def _changes(cfg: Config, root: Path, target: str, source: str | None) -> repo.ChangeSet:
    try:
        return repo.collect(
            root,
            target,
            source,
            budget=repo.ContextBudget(
                context_lines=cfg.run.diff_context_lines,
                max_chars=cfg.run.diff_max_chars,
                inline_max_lines=cfg.run.inline_max_lines,
                inline_max_total_chars=cfg.run.inline_max_total_chars,
            ),
            excludes=cfg.run.exclude_globs,
            resolve_references=cfg.run.resolve_references,
        )
    except repo.GitError as exc:
        raise CLIError(f"Git error: {exc}", _depth_hint(root)) from exc


def _depth_hint(root: Path) -> str:
    """Both runners clone shallow by default, and every git failure that causes
    looks like something else — a branch that does not exist, a diff with no
    branch point. Say it once, where the failure surfaces."""
    try:
        if not repo.is_shallow(root):
            return ""
    except repo.GitError:
        return ""
    return (
        "This clone is shallow, so the branch point may simply be missing. "
        "Deepen it (git fetch --unshallow, or GIT_DEPTH: 0 in .gitlab-ci.yml, "
        "or fetch-depth: 0 for actions/checkout)."
    )


def _provider_hint(cfg: Config) -> str:
    """Which file the provider came from — or the one that does not exist yet,
    which on a fresh machine is the whole of the problem."""
    if cfg.provider_source:
        return f"The provider is configured in {cfg.provider_source}."
    return (
        f"There is no provider file yet: run `roboviewer init`, which asks for "
        f"the address, the key and the model and writes {provider_config_path()}."
    )


def _plan(
    cfg: Config, root: Path, changes: repo.ChangeSet, items: list[ChecklistItem]
) -> RunPlan:
    """Everything that can still fail, gathered before the first request.

    A broken prompt template, a misspelled format or a missing key costs a second
    here; found later it costs the whole token bill, with nowhere left to write
    the report.
    """
    templates = overrides.templates_dir(cfg, root)
    try:
        prompts = Prompts.for_run(cfg, root)
        prompts.validate(items, changes)
    except PromptError as exc:
        raise CLIError(f"Prompt error: {exc}") from exc

    try:
        renders.prepare(cfg.run.report_formats, templates)
    except renders.RenderError as exc:
        raise CLIError(f"Report error: {exc}") from exc

    try:
        runner = OpenAIAgentRunner(
            cfg.provider, cfg.run, root, changes.comparison.base_sha, changes.comparison.source_ref
        )
    except RuntimeError as exc:
        raise CLIError(f"Provider error: {exc}", _provider_hint(cfg)) from exc

    return RunPlan(
        cfg=cfg,
        changes=changes,
        items=items,
        prompts=prompts,
        runner=runner,
        templates_dir=templates,
    )


async def _review(plan: RunPlan, verbose: bool, observer: RunObserver = SILENT) -> int:
    console.run_header(plan.cfg)
    pipeline = ReviewPipeline(
        plan.cfg,
        plan.changes,
        plan.items,
        plan.runner,
        plan.prompts,
        observer=Broadcast([console.Console(verbose), observer]),
    )
    try:
        run = await pipeline.execute()
    finally:
        await plan.runner.aclose()

    directory = output_dir_for(plan.cfg, plan.changes.comparison.root, run.run_id)
    try:
        reports = save(run, directory, plan.cfg.run.report_formats, plan.templates_dir)
    except renders.RenderError as exc:
        # The run itself is already on disk; only the readable part is missing
        console.error(f"Report failed to render: {exc}", f"Run data saved: {directory}")
        return exit_codes.SETUP
    console.summary(run, reports, directory)
    threshold = plan.cfg.run.fail_on
    console.gate_result(exit_codes.blocking(run, threshold), threshold)
    return exit_codes.exit_code(run, threshold)


if __name__ == "__main__":
    raise SystemExit(main())
