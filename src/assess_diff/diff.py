"""
The diff as values: parsing `git diff -U0` output and rebuilding each changed file's whole content as rows.

Everything here is pure. `assess_diff.git` runs git and reads the working tree, then hands the text to these functions.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from itertools import zip_longest
from typing import assert_never


class Side(StrEnum):
    """Which version of a file a line number counts in: the base (`old`) or the working tree (`new`)."""

    OLD = "old"
    NEW = "new"


class LineKind(StrEnum):
    CONTEXT = "context"
    ADDED = "added"
    REMOVED = "removed"


class FileStatus(StrEnum):
    ADDED = "added"
    DELETED = "deleted"
    MODIFIED = "modified"
    RENAMED = "renamed"
    UNTRACKED = "untracked"


@dataclass(frozen=True, slots=True)
class Line:
    """
    One row of a file in the unified view.
    A context line has both numbers, an added line only `new`, a removed line only `old`.
    `hunk` is the index of the hunk the line belongs to, or `None` for a context line outside every hunk.
    """

    kind: LineKind
    old: int | None
    new: int | None
    text: str
    hunk: int | None = None

    @property
    def side(self) -> Side:
        """The side a comment on this line anchors to: removed lines exist only in the base."""
        return Side.OLD if self.kind is LineKind.REMOVED else Side.NEW

    @property
    def number(self) -> int:
        """This line's number on its `side`."""
        number = self.old if self.kind is LineKind.REMOVED else self.new
        if number is None:
            raise ValueError(f"A {self.kind} line has no number on its own side: {self!r}")
        return number


@dataclass(frozen=True, slots=True)
class Hunk:
    """One change region from a `-U0` diff: the lines it removes from the base and adds in the working tree."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    removed: tuple[str, ...]
    added: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FileChange:
    """One file's entry in a `git diff -U0` output, before its whole content is known."""

    path: str
    old_path: str | None
    status: FileStatus
    is_binary: bool
    hunks: tuple[Hunk, ...]


@dataclass(frozen=True, slots=True)
class FileDiff:
    """One changed file, with every row of its content: context, removed and added lines, in display order."""

    path: str
    old_path: str | None
    status: FileStatus
    lines: tuple[Line, ...]
    hunk_count: int
    placeholder: str | None = None
    """Why the file's content isn't shown (a binary or very large file), or `None` when `lines` holds it."""

    additions: int = field(init=False)
    deletions: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "additions", sum(line.kind is LineKind.ADDED for line in self.lines))
        object.__setattr__(self, "deletions", sum(line.kind is LineKind.REMOVED for line in self.lines))


class BaseMode(StrEnum):
    """What the working tree is compared with: the last commit, or where the branch left the default branch."""

    HEAD = "head"
    MERGE_BASE = "merge-base"


@dataclass(frozen=True, slots=True)
class Base:
    mode: BaseMode
    commit: str
    label: str
    """What the reader knows the base by: `HEAD`, or the default branch the merge base is with."""

    @property
    def short(self) -> str:
        return self.commit[:7]


@dataclass(frozen=True, slots=True)
class Diff:
    base: Base
    files: tuple[FileDiff, ...]


@dataclass(frozen=True, slots=True)
class SplitRow:
    """One row of the split view: the base's line on the left, the working tree's on the right, either may be absent."""

    left: Line | None
    right: Line | None

    @property
    def primary(self) -> Line:
        """The line a cursor on this row points at: the working tree's, when the row has one."""
        line = self.right or self.left
        if line is None:
            raise ValueError("A split row has a line on at least one side")
        return line


HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", '"': '"', "\\": "\\"}


def unquote_path(path: str) -> str:
    """
    A path as git prints it in a diff header, with git's C-style quoting undone.
    git quotes a path holding a double quote, a backslash or a control character
    (and, unless `core.quotePath` is off, any byte outside ASCII), writing bytes as octal escapes.
    """
    if not (path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out.extend(char.encode())
            index += 1
            continue
        escaped = body[index + 1]
        if escaped in ESCAPES:
            out.extend(ESCAPES[escaped].encode())
            index += 2
        else:
            out.append(int(body[index + 1 : index + 4], 8))
            index += 4
    return out.decode()


def strip_prefix(path: str) -> str | None:
    """
    A `---`/`+++` header's path without its `a/` or `b/` prefix, or `None` for `/dev/null`.
    git ends the header with a tab when the path holds a space, for GNU patch's sake.
    """
    path = path.removesuffix("\t")
    if path == "/dev/null":
        return None
    return unquote_path(path)[2:]


def parse_diff(text: str) -> tuple[FileChange, ...]:
    """
    Parse the output of `git diff -U0 --no-color --no-ext-diff -M --src-prefix=a/ --dst-prefix=b/`.

    Paths come from the `---`/`+++` and `rename from`/`rename to` headers,
    which hold one path each, rather than from the `diff --git` line, where a path with a space in it is ambiguous.
    """
    return tuple(parse_file(block) for block in file_blocks(text.splitlines()))


def file_blocks(lines: list[str]) -> Iterator[list[str]]:
    """Each file's lines in a diff, from its `diff --git` line up to the next one."""
    block: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and block:
            yield block
            block = []
        block.append(line)
    if block:
        yield block


def parse_file(block: list[str]) -> FileChange:
    old_path: str | None = None
    new_path: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    is_new = is_deleted = is_binary = False
    hunks: list[Hunk] = []

    index = 1
    while index < len(block) and not block[index].startswith("@@"):
        line = block[index]
        if line.startswith("new file mode"):
            is_new = True
        elif line.startswith("deleted file mode"):
            is_deleted = True
        elif line.startswith("rename from "):
            rename_from = unquote_path(line.removeprefix("rename from "))
        elif line.startswith("rename to "):
            rename_to = unquote_path(line.removeprefix("rename to "))
        elif line.startswith("--- "):
            old_path = strip_prefix(line.removeprefix("--- "))
        elif line.startswith("+++ "):
            new_path = strip_prefix(line.removeprefix("+++ "))
        elif line.startswith("Binary files "):
            is_binary = True
        index += 1

    while index < len(block):
        match = HUNK_HEADER.match(block[index])
        if match is None:
            raise ValueError(f"Expected a hunk header in a diff, but got {block[index]!r}")
        old_start, old_count, new_start, new_count = (
            int(match[1]),
            int(match[2] or 1),
            int(match[3]),
            int(match[4] or 1),
        )
        index += 1
        removed: list[str] = []
        added: list[str] = []
        while index < len(block) and not block[index].startswith("@@"):
            line = block[index]
            if line.startswith("-"):
                removed.append(line[1:])
            elif line.startswith("+"):
                added.append(line[1:])
            # "\ No newline at end of file" says nothing a reviewer needs.
            index += 1
        hunks.append(Hunk(old_start, old_count, new_start, new_count, tuple(removed), tuple(added)))

    path = rename_to or new_path or old_path or binary_path(block[0])
    if is_new:
        status = FileStatus.ADDED
    elif is_deleted:
        status = FileStatus.DELETED
    elif rename_from is not None:
        status = FileStatus.RENAMED
    else:
        status = FileStatus.MODIFIED
    return FileChange(
        path=path,
        old_path=rename_from,
        status=status,
        is_binary=is_binary,
        hunks=tuple(hunks),
    )


def binary_path(header: str) -> str:
    """
    The path of a file whose diff has no `---`/`+++` headers, a binary file or a pure mode change,
    from its `diff --git a/<path> b/<path>` line, where both halves are the same path.
    """
    body = header.removeprefix("diff --git ")
    if body.startswith('"'):
        end = body.index('"', 1)
        while body[end - 1] == "\\":
            end = body.index('"', end + 1)
        return unquote_path(body[: end + 1])[2:]
    half = (len(body) - 1) // 2
    return body[2:half]


def build_lines(hunks: tuple[Hunk, ...], new_lines: tuple[str, ...]) -> tuple[Line, ...]:
    """
    Every row of a file in the unified view: the working tree's lines, with each hunk's removed lines
    put back in front of the lines that replaced them.

    Lines outside every hunk are the same in both versions, so the working tree's content and the hunks
    are all it takes; the base's content is never read.
    A `-U0` hunk that removes lines without adding any has a `new_start` of the line *before* the removal.
    """
    rows: list[Line] = []
    old_number = 1
    new_number = 1

    def context_through(last: int) -> None:
        nonlocal old_number, new_number
        while new_number <= min(last, len(new_lines)):
            rows.append(Line(LineKind.CONTEXT, old_number, new_number, new_lines[new_number - 1]))
            old_number += 1
            new_number += 1

    for index, hunk in enumerate(hunks):
        context_through(hunk.new_start if hunk.new_count == 0 else hunk.new_start - 1)
        old_number = hunk.old_start if hunk.old_count > 0 else old_number
        for text in hunk.removed:
            rows.append(Line(LineKind.REMOVED, old_number, None, text, hunk=index))
            old_number += 1
        new_number = hunk.new_start if hunk.new_count > 0 else new_number
        for text in hunk.added:
            rows.append(Line(LineKind.ADDED, None, new_number, text, hunk=index))
            new_number += 1
    context_through(len(new_lines))
    return tuple(rows)


def added_file(path: str, new_lines: tuple[str, ...], status: FileStatus = FileStatus.UNTRACKED) -> FileDiff:
    """A file that exists only in the working tree, such as an untracked one: every line added, in one hunk."""
    lines = tuple(Line(LineKind.ADDED, None, number, text, hunk=0) for number, text in enumerate(new_lines, start=1))
    return FileDiff(path=path, old_path=None, status=status, lines=lines, hunk_count=1 if lines else 0)


def placeholder_file(change: FileChange, reason: str) -> FileDiff:
    return FileDiff(
        path=change.path,
        old_path=change.old_path,
        status=change.status,
        lines=(),
        hunk_count=0,
        placeholder=reason,
    )


def file_diff(change: FileChange, new_lines: tuple[str, ...]) -> FileDiff:
    """A changed file's rows, given the working tree's lines (empty for a deleted file)."""
    return FileDiff(
        path=change.path,
        old_path=change.old_path,
        status=change.status,
        lines=build_lines(change.hunks, new_lines),
        hunk_count=len(change.hunks),
    )


def split_rows(lines: tuple[Line, ...]) -> tuple[SplitRow, ...]:
    """
    The unified rows paired for the split view: each run of removed lines beside the added lines after it,
    as GitHub pairs them, and each context line on both sides.
    """
    rows: list[SplitRow] = []
    removed: list[Line] = []
    added: list[Line] = []

    def flush() -> None:
        rows.extend(SplitRow(left, right) for left, right in zip_longest(removed, added))
        removed.clear()
        added.clear()

    for line in lines:
        match line.kind:
            case LineKind.REMOVED:
                if added:
                    flush()
                removed.append(line)
            case LineKind.ADDED:
                added.append(line)
            case LineKind.CONTEXT:
                flush()
                rows.append(SplitRow(line, line))
            case _ as unreachable:
                assert_never(unreachable)
    flush()
    return tuple(rows)
