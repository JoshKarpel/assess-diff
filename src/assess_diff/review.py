"""
The review as a value: its comments, their anchors in the diff, and the pure edits the UI makes to it.

A `Review` is also what's written to disk, so it's a pydantic model, declared once for both.
`parse_review` reads one from disk, migrating a file an older version wrote.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import FileDiff
from assess_diff.diff import Line
from assess_diff.diff import LineKind
from assess_diff.diff import Side


class NewAnchor(BaseModel):
    """
    Where a comment on the working tree's lines points: a range of lines in one file,
    as GitHub's `start_line`, `line` and `side` do.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    side: Literal[Side.NEW]
    start: int
    end: int
    # Always null; kept so both kinds of anchor are written in one shape.
    base_commit: None


class OldAnchor(BaseModel):
    """
    Where a comment on removed lines points: a range of the base's lines in one file.
    `base_commit` is the commit those numbers count in, since they change with the base.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    side: Literal[Side.OLD]
    start: int
    end: int
    base_commit: str


class FileAnchor(BaseModel):
    """Where a comment on a whole file points: the file, on no side and at no lines."""

    model_config = ConfigDict(frozen=True)

    path: str
    side: None


type LineAnchor = NewAnchor | OldAnchor
Anchor = Annotated[NewAnchor | OldAnchor | FileAnchor, Field(discriminator="side")]


class ExcerptLine(BaseModel):
    """A line the commenter selected, as it was when the comment was made."""

    model_config = ConfigDict(frozen=True)

    kind: LineKind
    text: str


class Comment(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    """Assigned in creation order and never reused, so one comment can refer to another as `#3`."""
    anchor: Anchor
    excerpt: tuple[ExcerptLine, ...]
    body: str
    created: datetime


class Review(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal[1]
    id: str
    name: str
    """What the reader calls the review; the time it started, until they rename it."""
    project: Path
    """The repository's common git directory, which every worktree of it shares."""
    worktree: Path
    branch: str | None
    base_mode: BaseMode
    """The base last reviewed against, which a resumed review and its export use."""
    created: datetime
    updated: datetime
    summary: str
    comments: tuple[Comment, ...]
    next_number: int

    @classmethod
    def start(
        cls,
        review_id: str,
        project: Path,
        worktree: Path,
        branch: str | None,
        base_mode: BaseMode,
        now: datetime,
    ) -> Review:
        return cls(
            version=CURRENT_VERSION,
            id=review_id,
            name=default_name(now),
            project=project,
            worktree=worktree,
            branch=branch,
            base_mode=base_mode,
            created=now,
            updated=now,
            summary="",
            comments=(),
            next_number=1,
        )

    def comment(self, number: int) -> Comment | None:
        return next((comment for comment in self.comments if comment.number == number), None)


CURRENT_VERSION = 1


class UnreadableReview(Exception):
    """A review file that can't be read: it isn't a review, or a newer version of assess-diff wrote it."""


type Migration = Callable[[Mapping[str, object]], dict[str, object]]
"""A review file's document at one version, rewritten as the next version would have written it."""

# Each version's migration to the next, by the version it migrates from.
MIGRATIONS: Mapping[int, Migration] = {}


def parse_review(content: bytes, migrations: Mapping[int, Migration] = MIGRATIONS) -> Review:
    """
    A review from a file's content, migrated to the current version if an older one wrote it.

    Raises:
        UnreadableReview: If a newer version wrote it, or it isn't a review at all.
        pydantic.ValidationError: If it's this version's review but has a field missing or malformed.
    """
    document = json.loads(content)
    if not isinstance(document, dict):
        raise UnreadableReview("A review file holds a JSON object")
    # `type(...) is int` rather than `isinstance`, and checked even at the current version,
    # since JSON's `true` and `1.0` compare equal to 1 (and `true` hashes as 1 in `migrations`), but no version wrote them.
    while type(version := document.get("version")) is not int or version != CURRENT_VERSION:
        migrate = migrations.get(version) if type(version) is int else None
        if migrate is None:
            raise UnreadableReview(
                f"This review is version {version!r}, and this assess-diff reads up to version {CURRENT_VERSION}"
            )
        document = migrate(document)
    return Review.model_validate(document)


def default_name(started: datetime) -> str:
    """A new review's name: when it started, in the reader's local time, to the minute."""
    return started.astimezone().strftime("%Y-%m-%d %H:%M")


def rename(review: Review, name: str) -> Review:
    return review.model_copy(update={"name": name})


def add_comment(review: Review, anchor: Anchor, excerpt: tuple[ExcerptLine, ...], now: datetime) -> Review:
    """`review` with a new, empty comment at `anchor`, numbered `review.next_number`."""
    comment = Comment(number=review.next_number, anchor=anchor, excerpt=excerpt, body="", created=now)
    return review.model_copy(update={"comments": (*review.comments, comment), "next_number": review.next_number + 1})


def edit_comment(review: Review, number: int, edit: Callable[[str], str]) -> Review:
    comments = tuple(
        comment.model_copy(update={"body": edit(comment.body)}) if comment.number == number else comment
        for comment in review.comments
    )
    return review.model_copy(update={"comments": comments})


def delete_comment(review: Review, number: int) -> Review:
    return review.model_copy(
        update={"comments": tuple(comment for comment in review.comments if comment.number != number)}
    )


def edit_summary(review: Review, edit: Callable[[str], str]) -> Review:
    return review.model_copy(update={"summary": edit(review.summary)})


def anchor_for(
    file: FileDiff, lines: tuple[Line, ...], side: Side, base_commit: str
) -> tuple[Anchor, tuple[ExcerptLine, ...]]:
    """
    The anchor and excerpt for a selection of `lines` in `file`, anchored on `side`.
    The excerpt keeps every selected line, in the unified view's order,
    while the anchor's range covers only the lines on `side`, which the caller ensures `lines` includes.
    """
    numbers = [line.number for line in lines if line.side is side]
    start, end = min(numbers), max(numbers)
    anchor: Anchor = (
        OldAnchor(path=file.path, side=Side.OLD, start=start, end=end, base_commit=base_commit)
        if side is Side.OLD
        else NewAnchor(path=file.path, side=Side.NEW, start=start, end=end, base_commit=None)
    )
    selected = set(lines)
    excerpt = tuple(ExcerptLine(kind=line.kind, text=line.text) for line in file.lines if line in selected)
    return anchor, excerpt


def anchored_lines(file: FileDiff, anchor: LineAnchor) -> tuple[Line, ...]:
    """The lines of `file` on the anchor's side within its range, as they are now."""
    return tuple(line for line in file.lines if line.side is anchor.side and anchor.start <= line.number <= anchor.end)


def is_outdated(comment: Comment, diff: Diff) -> bool:
    """
    Whether what a comment was made on has changed since, so where it points can't be trusted:
    its file left the diff, or for a comment on lines, its old-side lines count in another base,
    or the text at its range differs.
    """
    anchor = comment.anchor
    file = next((file for file in diff.files if file.path == anchor.path), None)
    if file is None:
        return True
    if isinstance(anchor, FileAnchor):
        return False
    if isinstance(anchor, OldAnchor) and anchor.base_commit != diff.base.commit:
        return True
    kinds = {LineKind.REMOVED} if anchor.side is Side.OLD else {LineKind.ADDED, LineKind.CONTEXT}
    then = [line.text for line in comment.excerpt if line.kind in kinds]
    now = [line.text for line in anchored_lines(file, anchor)]
    return then != now
