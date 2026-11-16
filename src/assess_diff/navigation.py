"""
The diff view as a sequence of positions the cursor can be on, and how the cursor moves through them.

A `Document` lays out the summary, each file's header, its rows (in the unified or split view,
of the whole file or of its hunks with their context), and each comment after the row it ends on, in display order.
The cursor is a position value rather than an index, so it survives the diff reloading under it.

One cursor over every position, rather than counterweight focus on each hunk,
since reaching a line between hunks would then need a second cursor handed off across components.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from typing import assert_never

from assess_diff.diff import Diff
from assess_diff.diff import FileDiff
from assess_diff.diff import Line
from assess_diff.diff import SplitRow
from assess_diff.diff import split_rows
from assess_diff.review import Anchor
from assess_diff.review import Comment
from assess_diff.review import FileAnchor


class ViewMode(StrEnum):
    UNIFIED = "unified"
    SPLIT = "split"


class Scope(StrEnum):
    """How much of each file the view shows: all of it, or only its hunks and their context."""

    WHOLE = "whole"
    CONTEXT = "context"


CONTEXT_ROWS = 3
"""How many unchanged rows `Scope.CONTEXT` shows either side of a change, as git and GitHub do by default."""

type Row = Line | SplitRow


@dataclass(frozen=True, slots=True)
class SummaryAt:
    """The review's top-level comment, at the top of the view."""


@dataclass(frozen=True, slots=True)
class HeaderAt:
    path: str


@dataclass(frozen=True, slots=True)
class RowAt:
    path: str
    index: int


@dataclass(frozen=True, slots=True)
class CommentAt:
    number: int


type Position = SummaryAt | HeaderAt | RowAt | CommentAt


def row_lines(row: Row) -> tuple[Line, ...]:
    """The distinct lines a row shows: one in the unified view, one or two in the split view."""
    match row:
        case Line():
            return (row,)
        case SplitRow(left=left, right=right):
            return tuple(dict.fromkeys(line for line in (left, right) if line is not None))
        case _ as unreachable:
            assert_never(unreachable)


def primary(row: Row) -> Line:
    """The line a cursor on `row` points at, which a comment started there anchors to."""
    match row:
        case Line():
            return row
        case SplitRow():
            return row.primary
        case _ as unreachable:
            assert_never(unreachable)


def rows_of(file: FileDiff, mode: ViewMode) -> tuple[Row, ...]:
    match mode:
        case ViewMode.UNIFIED:
            return file.lines
        case ViewMode.SPLIT:
            return split_rows(file.lines)
        case _ as unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class FileLayout:
    file: FileDiff
    rows: tuple[Row, ...]
    placed: tuple[tuple[int, tuple[int, ...]], ...]
    """
    For each row index that has comments after it, their numbers in creation order;
    index `-1` holds the comments drawn under the header, for a file with no row to put them after.
    """
    hunk_starts: frozenset[int]
    hidden: tuple[tuple[int, int], ...]
    """
    For each row index with unchanged rows hidden before it, how many,
    with index `len(rows)` for those hidden after the last row.
    Each hidden row is one unchanged line, in either view.
    """

    def comments_after(self, index: int) -> tuple[int, ...]:
        return next((numbers for at, numbers in self.placed if at == index), ())

    def hidden_before(self, index: int) -> int:
        """How many unchanged rows are hidden between row `index` and the row before it (or the file's start)."""
        return next((count for at, count in self.hidden if at == index), 0)


@dataclass(frozen=True, slots=True)
class Document:
    diff: Diff
    """The diff this lays out, so whoever has a document has the diff too."""
    mode: ViewMode
    scope: Scope
    files: tuple[FileLayout, ...]
    orphans: tuple[int, ...]
    """Comments on files no longer in the diff, drawn after every file."""
    positions: tuple[Position, ...]
    stops: tuple[int, ...]
    """The indexes of the positions Tab stops on: the summary, the first row of each hunk, and each comment."""
    indexes: dict[Position, int] = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "indexes", {position: index for index, position in enumerate(self.positions)})

    def file(self, path: str) -> FileLayout | None:
        return next((layout for layout in self.files if layout.file.path == path), None)

    def layout_of(self, row: RowAt) -> FileLayout:
        """
        The layout of the file `row` is in, which a cursor recovered against this document always names.

        Raises:
            ValueError: If no file here has `row`'s path.
        """
        layout = self.file(row.path)
        if layout is None:
            raise ValueError(f"{row!r} isn't in this document")
        return layout

    @property
    def comment_stops(self) -> tuple[int, ...]:
        """The indexes of the positions that are comments, which `n` and `N` jump between."""
        return tuple(index for index, position in enumerate(self.positions) if isinstance(position, CommentAt))


def place(rows: tuple[Row, ...], comment: Comment) -> int:
    """
    The index of the row a comment is drawn after: the one showing the last line of its range,
    or, when that line is gone, the last row before where it was, or `-1` when there's none,
    which is where a comment on the whole file goes too, under its header.
    """
    anchor = comment.anchor
    if isinstance(anchor, FileAnchor):
        return -1
    best = -1
    for index, row in enumerate(rows):
        for line in row_lines(row):
            if line.side is anchor.side and line.number <= anchor.end:
                best = index
                if line.number == anchor.end:
                    return index
    return best


def commented(rows: tuple[Row, ...], comment: Comment) -> Iterator[int]:
    """
    The indexes of the rows a comment is about, which `folded` keeps in view:
    those showing a line of its range, and the one it's drawn after,
    which for an outdated comment is the last row before where its lines were.
    """
    anchor = comment.anchor
    if isinstance(anchor, FileAnchor):
        return
    for index, row in enumerate(rows):
        if any(line.side is anchor.side and anchor.start <= line.number <= anchor.end for line in row_lines(row)):
            yield index
    if (after := place(rows, comment)) >= 0:
        yield after


def folded(rows: tuple[Row, ...], comments: tuple[Comment, ...]) -> tuple[tuple[Row, ...], tuple[tuple[int, int], ...]]:
    """
    A file's `rows` with each run of two or more unchanged ones more than `CONTEXT_ROWS` from a change hidden,
    and how many are hidden before each row that's kept, as `FileLayout.hidden` holds them.
    A lone row isn't hidden, since the line saying so would take as much room as it does.
    The rows `comments` are about stay in view with their context too,
    so no comment is drawn after a row far from the lines it's about.
    """
    wanted = {index for index, row in enumerate(rows) if any(line.hunk is not None for line in row_lines(row))}
    for comment in comments:
        wanted.update(commented(rows, comment))
    near = {
        close
        for index in wanted
        for close in range(max(0, index - CONTEXT_ROWS), min(len(rows), index + CONTEXT_ROWS + 1))
    }
    # Either side of a lone row is a row that's kept or one of the file's ends.
    edged = near | {-1, len(rows)}
    kept = sorted(near | {index for index in range(len(rows)) if index - 1 in edged and index + 1 in edged})
    hidden: list[tuple[int, int]] = []
    previous = -1
    for shown, index in enumerate(kept):
        if index > previous + 1:
            hidden.append((shown, index - previous - 1))
        previous = index
    if len(rows) > previous + 1:
        hidden.append((len(kept), len(rows) - previous - 1))
    return tuple(rows[index] for index in kept), tuple(hidden)


def hunk_starts(rows: tuple[Row, ...]) -> frozenset[int]:
    starts: set[int] = set()
    previous: frozenset[int] = frozenset()
    for index, row in enumerate(rows):
        hunks = frozenset(line.hunk for line in row_lines(row) if line.hunk is not None)
        if hunks - previous:
            starts.add(index)
        previous = hunks
    return frozenset(starts)


def layout_file(file: FileDiff, comments: tuple[Comment, ...], mode: ViewMode, scope: Scope) -> FileLayout:
    rows = rows_of(file, mode)
    hidden: tuple[tuple[int, int], ...] = ()
    match scope:
        case Scope.WHOLE:
            pass
        case Scope.CONTEXT:
            rows, hidden = folded(rows, comments)
        case _ as unreachable:
            assert_never(unreachable)
    placed: dict[int, list[int]] = {}
    for comment in comments:
        placed.setdefault(place(rows, comment), []).append(comment.number)
    return FileLayout(
        file=file,
        rows=rows,
        placed=tuple((index, tuple(numbers)) for index, numbers in sorted(placed.items())),
        hunk_starts=hunk_starts(rows),
        hidden=hidden,
    )


def placements(comments: tuple[Comment, ...]) -> tuple[tuple[int, Anchor], ...]:
    """What `build_document` reads of each comment: its number and its anchor, which a comment's body never changes."""
    return tuple((comment.number, comment.anchor) for comment in comments)


def build_document(diff: Diff, comments: tuple[Comment, ...], mode: ViewMode, scope: Scope) -> Document:
    """
    The positions of `diff` in `mode` and `scope`, with `comments` placed among them.
    It reads only what `placements` returns of the comments, which the app memoizes it on,
    so that typing in a comment doesn't lay out the whole diff again: reading more of them here means returning it there.
    """
    paths = {file.path for file in diff.files}
    files = tuple(
        layout_file(file, tuple(comment for comment in comments if comment.anchor.path == file.path), mode, scope)
        for file in diff.files
    )
    orphans = tuple(comment.number for comment in comments if comment.anchor.path not in paths)

    positions: list[Position] = [SummaryAt()]
    stops: list[int] = [0]
    for position, is_stop in document_positions(files, orphans):
        if is_stop:
            stops.append(len(positions))
        positions.append(position)
    return Document(
        diff=diff,
        mode=mode,
        scope=scope,
        files=files,
        orphans=orphans,
        positions=tuple(positions),
        stops=tuple(stops),
    )


def document_positions(files: tuple[FileLayout, ...], orphans: tuple[int, ...]) -> Iterator[tuple[Position, bool]]:
    """Every position after the summary in display order, each with whether Tab stops on it."""
    for layout in files:
        path = layout.file.path
        yield HeaderAt(path), False
        yield from ((CommentAt(number), True) for number in layout.comments_after(-1))
        for index in range(len(layout.rows)):
            yield RowAt(path, index), index in layout.hunk_starts
            yield from ((CommentAt(number), True) for number in layout.comments_after(index))
    yield from ((CommentAt(number), True) for number in orphans)


def moved(document: Document, cursor: Position, delta: int) -> Position:
    """The position `delta` positions from `cursor`, stopping at either end."""
    index = document.indexes[cursor] + delta
    return document.positions[max(0, min(index, len(document.positions) - 1))]


def next_of(document: Document, cursor: Position, stops: tuple[int, ...], *, forward: bool) -> Position:
    """
    The first of `stops` (indexes of positions) after `cursor`, or before it when not `forward`, wrapping at the ends,
    or `cursor` itself when there are no stops.
    """
    if not stops:
        return cursor
    index = document.indexes[cursor]
    ordered = stops if forward else tuple(reversed(stops))
    following = next((stop for stop in ordered if (stop > index if forward else stop < index)), ordered[0])
    return document.positions[following]


def next_stop(document: Document, cursor: Position, *, forward: bool) -> Position:
    """The next Tab stop: a hunk, a comment, or the summary."""
    return next_of(document, cursor, document.stops, forward=forward)


def next_comment(document: Document, cursor: Position, *, forward: bool) -> Position:
    """The next comment, skipping the hunks between."""
    return next_of(document, cursor, document.comment_stops, forward=forward)


def extended(document: Document, cursor: RowAt, delta: int) -> RowAt:
    """The row `delta` rows from `cursor` within its file, stopping at either end, for extending a selection."""
    layout = document.file(cursor.path)
    if layout is None:
        return cursor
    return RowAt(cursor.path, max(0, min(cursor.index + delta, len(layout.rows) - 1)))


def selection_span(cursor: RowAt, anchor: RowAt | None) -> tuple[int, int]:
    """The first and last row indexes selected from `anchor` (if it's in the cursor's file) to `cursor`."""
    start = anchor if anchor is not None and anchor.path == cursor.path else cursor
    return min(start.index, cursor.index), max(start.index, cursor.index)


def recovered_selection(document: Document, cursor: Position, selection: RowAt | None) -> RowAt | None:
    """
    The selection's other end when the document changes under it: clamped to the rows its file still has,
    or `None` once the cursor isn't on a row of that file, since a selection spans the rows of one file.
    """
    if selection is None or not isinstance(cursor, RowAt) or cursor.path != selection.path:
        return None
    return extended(document, selection, 0)


def recovered(document: Document, cursor: Position) -> Position:
    """
    Where a cursor goes when the document changes under it: where it was, if that position still exists,
    else the nearest row of its file, else its file's header, else the summary.
    """
    if cursor in document.indexes:
        return cursor
    match cursor:
        case RowAt(path=path, index=index):
            layout = document.file(path)
            if layout is None:
                return SummaryAt()
            if layout.rows:
                return RowAt(path, min(index, len(layout.rows) - 1))
            return HeaderAt(path)
        case _:
            return SummaryAt()


def converted(cursor: Position, before: Document, after: Document) -> Position:
    """
    The cursor's position in `after`, a layout of the same diff in another view or scope:
    on the same line, or where `after` hides it, on the nearest line it shows (the earlier, of two as near),
    or on the file's header when it shows none.
    """
    if not isinstance(cursor, RowAt):
        return cursor
    old_layout = before.file(cursor.path)
    new_layout = after.file(cursor.path)
    if old_layout is None or new_layout is None:
        return recovered(after, cursor)
    if not new_layout.rows:
        return HeaderAt(cursor.path)
    order = {line: index for index, line in enumerate(new_layout.file.lines)}
    target = order[primary(old_layout.rows[cursor.index])]
    distances = [min(abs(order[line] - target) for line in row_lines(row)) for row in new_layout.rows]
    return RowAt(cursor.path, distances.index(min(distances)))


def in_view(
    document: Document | None, cursor: Position, comments: tuple[Comment, ...], mode: ViewMode, scope: Scope
) -> Position:
    """
    Where the cursor goes when the view switches to `mode` or `scope`: on the same line, or the nearest shown,
    or where it is if nothing's shown.
    """
    if document is None:
        return cursor
    return converted(cursor, document, build_document(document.diff, comments, mode, scope))


def comment_row(document: Document | None, number: int) -> Position:
    """The row (or header) a comment is drawn after, to put the cursor on when the comment goes away."""
    if document is None:
        return SummaryAt()
    for layout in document.files:
        for index, numbers in layout.placed:
            if number in numbers:
                return RowAt(layout.file.path, index) if index >= 0 else HeaderAt(layout.file.path)
    return SummaryAt()
