"""
The `assess-diff` command: the shell that finds the repository, opens or starts a review, and runs the app,
plus the commands that list, print and delete reviews without it.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncIterator
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path

from counterweight import app
from without_cli import PATH
from without_cli import STR
from without_cli import FromEnv
from without_cli import Streams
from without_cli import argument
from without_cli import command
from without_cli import flag
from without_cli import group
from without_cli import once
from without_cli import option
from without_cli import optional
from without_cli import run

from assess_diff.diff import Diff
from assess_diff.export import to_markdown
from assess_diff.git import GitError
from assess_diff.git import Repository
from assess_diff.git import compute_diff
from assess_diff.git import default_branch
from assess_diff.git import find_repository
from assess_diff.git import resolve_base
from assess_diff.preferences import PreferencesFile
from assess_diff.preferences import preferences_path
from assess_diff.review import FOLLOW_HEAD
from assess_diff.review import FOLLOW_MERGE_BASE
from assess_diff.review import BaseChoice
from assess_diff.review import Review
from assess_diff.review import UnreadableReview
from assess_diff.storage import Listing
from assess_diff.storage import NoSuchReview
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewStore
from assess_diff.storage import Stored
from assess_diff.storage import Unreadable
from assess_diff.storage import for_project
from assess_diff.storage import reviews_directory
from assess_diff.ui.app import assess_app

COMMANDS = ("review", "list", "show", "delete")
ANSWERED = ("-h", "--help", "--version")
GROUP_OPTIONS = ("--data-home", "--config-home")


@dataclass(frozen=True, slots=True)
class Workspace:
    """What every command works with: where to write, where reviews are, and where preferences are."""

    streams: Streams
    store: ReviewStore
    preferences: PreferencesFile


@asynccontextmanager
async def workspace(streams: Streams, data_home: Path | None, config_home: Path | None) -> AsyncIterator[Workspace]:
    yield Workspace(
        streams=streams,
        store=ReviewStore(reviews_directory(data_home)),
        preferences=PreferencesFile(preferences_path(config_home)),
    )


def new_id() -> str:
    """A review id: a UUIDv7, so ids sort by when their reviews started."""
    return uuid.uuid7().hex


def now() -> datetime:
    return datetime.now(UTC)


async def target_branch(repository: Repository, base: str | None) -> str | None:
    """The branch to take the merge base with: `base` if given, else the default branch, or `None` if there's none."""
    if base is not None:
        return base
    try:
        return await default_branch(repository)
    except GitError:
        return None


def latest(reviews: tuple[Review, ...], repository: Repository) -> Review | None:
    """The current worktree's most recently updated of `reviews`, which are most recently updated first."""
    return next(
        (
            review
            for review in reviews
            if review.project == repository.common_dir and review.worktree == repository.worktree
        ),
        None,
    )


def report_unreadable(streams: Streams, unreadable: tuple[Unreadable, ...]) -> None:
    """Say which files in the store can't be read, on standard error, so a command's output is only its own."""
    for file in unreadable:
        streams.stderr.write(f"assess-diff: {file.message}\n")


async def current_diff(repository: Repository, review: Review, target: str | None) -> Diff:
    """The diff `review` reads against: the base it was last viewed against, with the merge base taken with `target`."""
    return await compute_diff(repository, await resolve_base(repository, review.base, target))


def starting_base(base: str | None) -> BaseChoice:
    """What a new review is against: the change since the last commit, unless `--base` named a branch to take the merge base with."""
    return FOLLOW_MERGE_BASE if base is not None else FOLLOW_HEAD


def opened(
    store: ReviewStore, listing: Listing, repository: Repository, new: bool, review_id: str | None
) -> Stored | None:
    """The review to open: the one `review_id` names, none for a `new` one, or else this worktree's latest."""
    if review_id is not None:
        return store.find(review_id)
    if new:
        return None
    found = latest(listing.reviews, repository)
    return None if found is None else store.load(found.id)


@command(
    "review",
    flag("--new", summary="Start a new review, rather than resuming this worktree's latest."),
    option("--review", optional(STR), summary="Open the review with this id (or the start of it)."),
    option("--base", optional(STR), summary="The branch to take the merge base with, if not the default branch."),
)
async def review_command(workspace: Workspace, new: bool, review_id: str | None, base: str | None) -> int:
    """Review this worktree's changes (the default command)."""
    repository = await find_repository(Path.cwd())
    target = await target_branch(repository, base)
    listing = workspace.store.listed()
    stored = opened(workspace.store, listing, repository, new, review_id)
    if stored is None:
        starting = starting_base(base)
        review = Review.start(new_id(), repository.common_dir, repository.worktree, repository.branch, starting, now())
        open_review = OpenReview(workspace.store, review, None, new_id, now)
    else:
        open_review = OpenReview(workspace.store, stored.review, stored.digest, new_id, now)
    try:
        diff = await current_diff(repository, open_review.review, target)
    except GitError:
        # The app computes it again as it starts, and shows the failure where `b` can switch to a base that works.
        diff = None
    # The streams are read here, not bound as `app`'s defaults are, so a test can give it a pseudo-terminal.
    await app(
        assess_app(
            repository,
            target,
            open_review,
            workspace.preferences,
            workspace.preferences.load(),
            diff,
            listing.unreadable,
        ),
        # Every stream is passed, since counterweight's defaults are the ones in place when it was first imported,
        # which the tests' runs in one process (mutmut's) have closed by the second.
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        stdout=sys.stdout,
        stderr=sys.stderr,
        title="assess-diff",
    )
    saved = open_review.review
    if open_review.digest is None:
        workspace.streams.stdout.write("Nothing was written, so no review was saved.\n")
    else:
        workspace.streams.stdout.write(
            f"Saved review “{saved.name}” ({saved.id[:12]}); `assess-diff show` prints it.\n"
        )
    return 0


@command("list")
async def list_command(workspace: Workspace) -> int:
    """List this repository's reviews, this worktree's first, each most recently updated first."""
    repository = await find_repository(Path.cwd())
    listing = workspace.store.listed()
    report_unreadable(workspace.streams, listing.unreadable)
    for review in for_project(listing.reviews, repository.common_dir, repository.worktree):
        here = "*" if review.worktree == repository.worktree else " "
        updated = review.updated.astimezone().strftime("%Y-%m-%d %H:%M")
        comments = f"{len(review.comments)} comment" + ("" if len(review.comments) == 1 else "s")
        workspace.streams.stdout.write(
            f"{here} {review.id[:12]}  {review.name}  {review.branch or 'detached HEAD'}  {comments}"
            f"  updated {updated}  {review.worktree}\n"
        )
    return 0


@command("show", argument("id", optional(STR), summary="The review's id, or the start of it."))
async def show_command(workspace: Workspace, review_id: str | None) -> int:
    """Print a review as Markdown: this worktree's latest, unless an id is given."""
    repository = await find_repository(Path.cwd())
    listing = workspace.store.listed()
    if review_id is None:
        # Only when choosing the latest, which might have been one of them; a review asked for by id is just that one.
        report_unreadable(workspace.streams, listing.unreadable)
    stored = opened(workspace.store, listing, repository, False, review_id)
    if stored is None:
        workspace.streams.stderr.write("This worktree has no reviews yet; `assess-diff` starts one.\n")
        return 1
    diff = await current_diff(repository, stored.review, await target_branch(repository, None))
    workspace.streams.stdout.write(to_markdown(stored.review, diff))
    return 0


@command("delete", argument("id", once(STR), summary="The review's id, or the start of it."))
async def delete_command(workspace: Workspace, review_id: str) -> int:
    """Delete a review, even one whose file can't be read."""
    found = workspace.store.matching(review_id)
    workspace.store.delete(found)
    workspace.streams.stdout.write(f"Deleted review {found}\n")
    return 0


cli = group(
    "assess-diff",
    option("--data-home", optional(PATH), sources=(FromEnv("XDG_DATA_HOME"),), summary="Where reviews are kept."),
    option(
        "--config-home", optional(PATH), sources=(FromEnv("XDG_CONFIG_HOME"),), summary="Where preferences are kept."
    ),
    state=workspace,
    commands=(review_command, list_command, show_command, delete_command),
    summary="Review an agent's working-tree diff in the terminal, and hand the review back as Markdown.",
)


def with_default_command(argv: Sequence[str]) -> list[str]:
    """
    `argv` with `review` put in front of the first argument that isn't one of the group's own options,
    when no command is named, so a bare `assess-diff` (or `assess-diff --new`) opens the app.
    """
    arguments = list(argv)
    index = 0
    while index < len(arguments):
        name, equals, _ = arguments[index].partition("=")
        if name not in GROUP_OPTIONS:
            break
        # `--data-home=/d` carries its value; `--data-home /d` takes the next argument.
        index += 1 if equals else 2
    rest = arguments[index:]
    if rest and (rest[0] in COMMANDS or rest[0] in ANSWERED):
        return arguments
    return [*arguments[:index], "review", *rest]


def main() -> None:
    """The `assess-diff` console script."""
    try:
        code = run(cli, argv=with_default_command(sys.argv[1:]))
    except (GitError, NoSuchReview, UnreadableReview) as error:
        sys.stderr.write(f"assess-diff: {error}\n")
        code = 1
    raise SystemExit(code)
