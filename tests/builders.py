"""Small diffs, reviews and comments built by hand, for tests of the pure modules that don't need git."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path

from assess_diff.diff import Base
from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.diff import Line
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.review import FOLLOW_MERGE_BASE
from assess_diff.review import Comment
from assess_diff.review import ExcerptLine
from assess_diff.review import FileAnchor
from assess_diff.review import NewAnchor
from assess_diff.review import OldAnchor
from assess_diff.review import Review

BASE_COMMIT = "c0ffee0123456789c0ffee0123456789c0ffee01"
STARTED = datetime(2026, 3, 14, 15, 9, 26, tzinfo=UTC)


def context(old: int, new: int, text: str) -> Line:
    return Line(LineKind.CONTEXT, old, new, text)


def removed(old: int, text: str, hunk: int = 0) -> Line:
    return Line(LineKind.REMOVED, old, None, text, hunk)


def added(new: int, text: str, hunk: int = 0) -> Line:
    return Line(LineKind.ADDED, None, new, text, hunk)


def modified(path: str, *lines: Line) -> FileDiff:
    hunks = {line.hunk for line in lines if line.hunk is not None}
    return FileDiff(path=path, old_path=None, status=FileStatus.MODIFIED, lines=lines, hunk_count=len(hunks))


def diff_of(*files: FileDiff, mode: BaseMode = BaseMode.MERGE_BASE, label: str = "trunk") -> Diff:
    return Diff(base=Base(mode=mode, commit=BASE_COMMIT, label=label), files=files)


def review(*comments: Comment, summary: str = "", branch: str | None = "feature/x") -> Review:
    started = Review.start(
        "0199aaaa-review", Path("/work/repo/.git"), Path("/work/repo"), branch, FOLLOW_MERGE_BASE, STARTED
    )
    next_number = max((comment.number for comment in comments), default=0) + 1
    return started.model_copy(update={"comments": comments, "summary": summary, "next_number": next_number})


def comment(
    number: int,
    path: str,
    start: int,
    end: int,
    excerpt: tuple[tuple[LineKind, str], ...],
    body: str = "",
    side: Side = Side.NEW,
) -> Comment:
    return Comment(
        number=number,
        anchor=(
            OldAnchor(path=path, side=Side.OLD, start=start, end=end, base_commit=BASE_COMMIT)
            if side is Side.OLD
            else NewAnchor(path=path, side=Side.NEW, start=start, end=end, base_commit=None)
        ),
        excerpt=tuple(ExcerptLine(kind=kind, text=text) for kind, text in excerpt),
        body=body,
        created=STARTED,
    )


def file_comment(number: int, path: str, body: str = "") -> Comment:
    return Comment(number=number, anchor=FileAnchor(path=path, side=None), excerpt=(), body=body, created=STARTED)
