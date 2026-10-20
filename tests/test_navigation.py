from __future__ import annotations

import pytest

from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.diff import SplitRow
from assess_diff.navigation import CommentAt
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import SummaryAt
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.navigation import comment_row
from assess_diff.navigation import converted
from assess_diff.navigation import extended
from assess_diff.navigation import hunk_starts
from assess_diff.navigation import in_mode
from assess_diff.navigation import moved
from assess_diff.navigation import next_comment
from assess_diff.navigation import next_stop
from assess_diff.navigation import place
from assess_diff.navigation import primary
from assess_diff.navigation import recovered
from assess_diff.navigation import row_lines
from assess_diff.navigation import selection_span
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import diff_of
from tests.builders import modified
from tests.builders import removed

APP = modified(
    "app.py",
    context(1, 1, "a"),
    removed(2, "b"),
    added(2, "B"),
    context(3, 3, "c"),
    added(4, "d", hunk=1),
)
NOTES = modified("notes.md", added(1, "hello"))
EMPTY = modified("empty.bin")

ON_B = comment(1, "app.py", 2, 2, ((LineKind.ADDED, "B"),))
ON_GONE = comment(2, "gone.py", 1, 1, ((LineKind.ADDED, "x"),))
ON_EMPTY = comment(3, "empty.bin", 1, 1, ((LineKind.ADDED, "x"),))


def test_document_lays_out_the_summary_headers_rows_and_comments_in_order() -> None:
    document = build_document(diff_of(APP, NOTES), (ON_B, ON_GONE), ViewMode.UNIFIED)

    assert document.positions == (
        SummaryAt(),
        HeaderAt("app.py"),
        RowAt("app.py", 0),
        RowAt("app.py", 1),
        RowAt("app.py", 2),
        CommentAt(1),
        RowAt("app.py", 3),
        RowAt("app.py", 4),
        HeaderAt("notes.md"),
        RowAt("notes.md", 0),
        CommentAt(2),
    )
    assert [document.positions[stop] for stop in document.stops] == [
        SummaryAt(),
        RowAt("app.py", 1),
        CommentAt(1),
        RowAt("app.py", 4),
        RowAt("notes.md", 0),
        CommentAt(2),
    ]
    assert document.orphans == (2,)


def test_comments_on_a_file_without_rows_go_under_its_header() -> None:
    document = build_document(diff_of(EMPTY), (ON_EMPTY,), ViewMode.UNIFIED)

    assert document.positions == (SummaryAt(), HeaderAt("empty.bin"), CommentAt(3))


def test_split_document_pairs_rows() -> None:
    document = build_document(diff_of(APP), (ON_B,), ViewMode.SPLIT)

    (layout,) = document.files
    assert layout.rows[1] == SplitRow(APP.lines[1], APP.lines[2])
    assert document.positions[2:7] == (
        RowAt("app.py", 0),
        RowAt("app.py", 1),
        CommentAt(1),
        RowAt("app.py", 2),
        RowAt("app.py", 3),
    )


def test_document_finds_a_file_by_path() -> None:
    document = build_document(diff_of(APP), (), ViewMode.UNIFIED)

    assert (document.file("app.py") is document.files[0], document.file("nope.py")) == (True, None)


def test_row_lines_of_a_split_context_row_is_its_one_line() -> None:
    line = APP.lines[0]

    assert row_lines(SplitRow(line, line)) == (line,)


def test_primary_of_a_unified_row_is_itself() -> None:
    assert primary(APP.lines[1]) == APP.lines[1]


@pytest.mark.parametrize(
    ("end", "side", "expected"),
    [(2, Side.NEW, 2), (3, Side.NEW, 3), (2, Side.OLD, 1), (9, Side.NEW, 4), (0, Side.NEW, -1)],
)
def test_place_after_the_row_ending_the_range_or_the_last_before_it(end: int, side: Side, expected: int) -> None:
    placed = comment(1, "app.py", end, end, (), side=side)

    assert place(APP.lines, placed) == expected


def test_hunk_starts_at_each_hunks_first_row() -> None:
    assert hunk_starts(APP.lines) == {1, 4}


def test_adjacent_hunks_each_start_a_stop() -> None:
    lines = (added(1, "x", hunk=0), added(2, "y", hunk=1))

    assert hunk_starts(lines) == {0, 1}


DOCUMENT = build_document(diff_of(APP, NOTES), (ON_B,), ViewMode.UNIFIED)


@pytest.mark.parametrize(
    ("start", "delta", "expected"),
    [
        (RowAt("app.py", 0), 1, RowAt("app.py", 1)),
        (RowAt("app.py", 2), 1, CommentAt(1)),
        (RowAt("app.py", 0), -1, HeaderAt("app.py")),
        (SummaryAt(), -1, SummaryAt()),
        (RowAt("notes.md", 0), 5, RowAt("notes.md", 0)),
    ],
)
def test_moved_steps_through_positions_and_stops_at_the_ends(start: Position, delta: int, expected: Position) -> None:
    assert moved(DOCUMENT, start, delta) == expected


def test_next_stop_goes_forward_and_wraps() -> None:
    assert [
        next_stop(DOCUMENT, position, 1) for position in (SummaryAt(), RowAt("app.py", 2), RowAt("notes.md", 0))
    ] == [
        RowAt("app.py", 1),
        CommentAt(1),
        SummaryAt(),
    ]


def test_next_stop_goes_backward_and_wraps() -> None:
    assert [next_stop(DOCUMENT, position, -1) for position in (SummaryAt(), CommentAt(1), RowAt("app.py", 1))] == [
        RowAt("notes.md", 0),
        RowAt("app.py", 1),
        SummaryAt(),
    ]


def test_extended_stays_within_the_file() -> None:
    assert [extended(DOCUMENT, RowAt("app.py", 3), delta) for delta in (1, 5, -9)] == [
        RowAt("app.py", 4),
        RowAt("app.py", 4),
        RowAt("app.py", 0),
    ]


def test_extended_from_a_file_no_longer_there_stays_put() -> None:
    assert extended(DOCUMENT, RowAt("gone.py", 2), 1) == RowAt("gone.py", 2)


def test_selection_span_runs_from_the_anchor_to_the_cursor_either_way() -> None:
    assert [
        selection_span(RowAt("app.py", 3), RowAt("app.py", 1)),
        selection_span(RowAt("app.py", 1), RowAt("app.py", 3)),
        selection_span(RowAt("app.py", 2), None),
        selection_span(RowAt("app.py", 2), RowAt("notes.md", 0)),
    ] == [(1, 3), (1, 3), (2, 2), (2, 2)]


@pytest.mark.parametrize(
    ("cursor", "expected"),
    [
        (RowAt("app.py", 2), RowAt("app.py", 2)),
        (RowAt("app.py", 40), RowAt("app.py", 4)),
        (RowAt("gone.py", 1), SummaryAt()),
        (CommentAt(7), SummaryAt()),
        (HeaderAt("gone.py"), SummaryAt()),
    ],
)
def test_recovered_keeps_the_cursor_or_finds_the_nearest_place(cursor: Position, expected: Position) -> None:
    assert recovered(DOCUMENT, cursor) == expected


def test_recovered_on_a_file_that_lost_its_rows_goes_to_its_header() -> None:
    document = build_document(diff_of(EMPTY), (), ViewMode.UNIFIED)

    assert recovered(document, RowAt("empty.bin", 3)) == HeaderAt("empty.bin")


def test_converted_keeps_the_cursor_on_the_same_line_across_views() -> None:
    unified = build_document(diff_of(APP), (), ViewMode.UNIFIED)
    split = build_document(diff_of(APP), (), ViewMode.SPLIT)

    assert [
        converted(RowAt("app.py", 2), unified, split),
        converted(RowAt("app.py", 1), unified, split),
        converted(RowAt("app.py", 1), split, unified),
        converted(RowAt("app.py", 3), split, unified),
        converted(HeaderAt("app.py"), unified, split),
    ] == [RowAt("app.py", 1), RowAt("app.py", 1), RowAt("app.py", 2), RowAt("app.py", 4), HeaderAt("app.py")]


def test_converted_from_a_file_not_in_both_documents_recovers() -> None:
    unified = build_document(diff_of(APP), (), ViewMode.UNIFIED)
    split = build_document(diff_of(NOTES), (), ViewMode.SPLIT)

    assert converted(RowAt("app.py", 1), unified, split) == SummaryAt()


TWO_COMMENTS = build_document(
    diff_of(APP, NOTES), (ON_B, comment(4, "notes.md", 1, 1, ((LineKind.ADDED, "hello"),))), ViewMode.UNIFIED
)


def test_next_comment_skips_hunks_and_wraps() -> None:
    assert [next_comment(TWO_COMMENTS, position, 1) for position in (SummaryAt(), CommentAt(1), CommentAt(4))] == [
        CommentAt(1),
        CommentAt(4),
        CommentAt(1),
    ]


def test_previous_comment_skips_hunks_and_wraps() -> None:
    assert [next_comment(TWO_COMMENTS, position, -1) for position in (RowAt("notes.md", 0), CommentAt(1))] == [
        CommentAt(1),
        CommentAt(4),
    ]


def test_next_comment_without_comments_stays_put() -> None:
    document = build_document(diff_of(APP), (), ViewMode.UNIFIED)

    assert next_comment(document, RowAt("app.py", 2), 1) == RowAt("app.py", 2)


def test_layout_of_a_row_in_another_document_refuses() -> None:
    with pytest.raises(ValueError, match="isn't in this document"):
        DOCUMENT.layout_of(RowAt("gone.py", 0))


def test_in_mode_converts_the_cursor_or_leaves_it_without_a_document() -> None:
    unified = build_document(diff_of(APP), (), ViewMode.UNIFIED)

    assert (
        in_mode(unified, RowAt("app.py", 2), (), ViewMode.SPLIT),
        in_mode(None, RowAt("app.py", 2), (), ViewMode.SPLIT),
    ) == (
        RowAt("app.py", 1),
        RowAt("app.py", 2),
    )


def test_comment_row_is_the_row_or_header_a_comment_follows() -> None:
    document = build_document(diff_of(EMPTY, APP), (ON_EMPTY, ON_B, ON_GONE), ViewMode.UNIFIED)

    assert [comment_row(document, number) for number in (3, 1, 2, 9)] == [
        HeaderAt("empty.bin"),
        RowAt("app.py", 2),
        SummaryAt(),
        SummaryAt(),
    ]
    assert comment_row(None, 1) == SummaryAt()
