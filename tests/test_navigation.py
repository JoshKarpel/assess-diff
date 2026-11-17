from __future__ import annotations

import pytest

from assess_diff.diff import FileDiff
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.diff import SplitRow
from assess_diff.navigation import CommentAt
from assess_diff.navigation import FileLayout
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import Scope
from assess_diff.navigation import SummaryAt
from assess_diff.navigation import ViewMode
from assess_diff.navigation import after_deleting
from assess_diff.navigation import build_document
from assess_diff.navigation import comment_row
from assess_diff.navigation import converted
from assess_diff.navigation import extended
from assess_diff.navigation import hunk_starts
from assess_diff.navigation import in_view
from assess_diff.navigation import lines_by_side
from assess_diff.navigation import moved
from assess_diff.navigation import next_comment
from assess_diff.navigation import next_stop
from assess_diff.navigation import place
from assess_diff.navigation import placements
from assess_diff.navigation import primary
from assess_diff.navigation import recovered
from assess_diff.navigation import recovered_selection
from assess_diff.navigation import row_lines
from assess_diff.navigation import selected_lines
from assess_diff.navigation import selection_span
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import diff_of
from tests.builders import file_comment
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
    document = build_document(diff_of(APP, NOTES), (ON_B, ON_GONE), ViewMode.UNIFIED, Scope.WHOLE)

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
    document = build_document(diff_of(EMPTY), (ON_EMPTY,), ViewMode.UNIFIED, Scope.WHOLE)

    assert document.positions == (SummaryAt(), HeaderAt("empty.bin"), CommentAt(3))
    assert [document.positions[stop] for stop in document.stops] == [SummaryAt(), CommentAt(3)]


def test_a_comment_on_a_whole_file_goes_under_its_header_before_its_rows() -> None:
    document = build_document(diff_of(APP), (ON_B, file_comment(4, "app.py")), ViewMode.UNIFIED, Scope.WHOLE)

    assert document.positions[1:4] == (HeaderAt("app.py"), CommentAt(4), RowAt("app.py", 0))
    assert comment_row(document, 4) == HeaderAt("app.py")


def test_split_document_pairs_rows() -> None:
    document = build_document(diff_of(APP), (ON_B,), ViewMode.SPLIT, Scope.WHOLE)

    (layout,) = document.files
    assert layout.rows[1] == SplitRow(APP.lines[1], APP.lines[2])
    assert document.positions[2:7] == (
        RowAt("app.py", 0),
        RowAt("app.py", 1),
        CommentAt(1),
        RowAt("app.py", 2),
        RowAt("app.py", 3),
    )


def test_placements_ignore_what_a_comment_says() -> None:
    excerpt = ((LineKind.ADDED, "x = 1"),)

    assert placements((comment(3, "a.py", 2, 4, excerpt, body="first"),)) == placements(
        (comment(3, "a.py", 2, 4, excerpt, body="first, then more"),)
    )


@pytest.mark.parametrize(
    ("number", "path", "start", "end", "side"),
    [
        (4, "a.py", 2, 4, Side.NEW),
        (3, "b.py", 2, 4, Side.NEW),
        (3, "a.py", 1, 4, Side.NEW),
        (3, "a.py", 2, 5, Side.NEW),
        (3, "a.py", 2, 4, Side.OLD),
    ],
    ids=["number", "path", "start", "end", "side"],
)
def test_placements_change_with_where_a_comment_is(number: int, path: str, start: int, end: int, side: Side) -> None:
    excerpt = ((LineKind.ADDED, "x = 1"),)

    assert placements((comment(3, "a.py", 2, 4, excerpt),)) != placements(
        (comment(number, path, start, end, excerpt, side=side),)
    )


def test_document_finds_a_file_by_path() -> None:
    document = build_document(diff_of(APP), (), ViewMode.UNIFIED, Scope.WHOLE)

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

    assert place(lines_by_side(APP.lines), placed) == expected


def test_hunk_starts_at_each_hunks_first_row() -> None:
    assert hunk_starts(APP.lines) == {1, 4}


def test_adjacent_hunks_each_start_a_stop() -> None:
    lines = (added(1, "x", hunk=0), added(2, "y", hunk=1))

    assert hunk_starts(lines) == {0, 1}


DOCUMENT = build_document(diff_of(APP, NOTES), (ON_B,), ViewMode.UNIFIED, Scope.WHOLE)


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
        next_stop(DOCUMENT, position, forward=True)
        for position in (SummaryAt(), RowAt("app.py", 2), RowAt("notes.md", 0))
    ] == [
        RowAt("app.py", 1),
        CommentAt(1),
        SummaryAt(),
    ]


def test_next_stop_goes_backward_and_wraps() -> None:
    assert [
        next_stop(DOCUMENT, position, forward=False) for position in (SummaryAt(), CommentAt(1), RowAt("app.py", 1))
    ] == [
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


@pytest.mark.parametrize(
    ("cursor", "selection", "expected"),
    [
        (RowAt("app.py", 1), RowAt("app.py", 3), RowAt("app.py", 3)),
        (RowAt("app.py", 1), RowAt("app.py", 9), RowAt("app.py", 4)),
        (RowAt("app.py", 1), None, None),
        (RowAt("notes.md", 0), RowAt("app.py", 3), None),
        (HeaderAt("app.py"), RowAt("app.py", 3), None),
    ],
)
def test_recovered_selection_is_clamped_to_its_file_and_dropped_once_the_cursor_leaves_it(
    cursor: Position, selection: RowAt | None, expected: RowAt | None
) -> None:
    assert recovered_selection(DOCUMENT, cursor, selection) == expected


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
    document = build_document(diff_of(EMPTY), (), ViewMode.UNIFIED, Scope.WHOLE)

    assert recovered(document, RowAt("empty.bin", 3)) == HeaderAt("empty.bin")


def test_converted_keeps_the_cursor_on_the_same_line_across_views() -> None:
    unified = build_document(diff_of(APP), (), ViewMode.UNIFIED, Scope.WHOLE)
    split = build_document(diff_of(APP), (), ViewMode.SPLIT, Scope.WHOLE)

    assert [
        converted(RowAt("app.py", 2), unified, split),
        converted(RowAt("app.py", 1), unified, split),
        converted(RowAt("app.py", 1), split, unified),
        converted(RowAt("app.py", 3), split, unified),
        converted(HeaderAt("app.py"), unified, split),
    ] == [RowAt("app.py", 1), RowAt("app.py", 1), RowAt("app.py", 2), RowAt("app.py", 4), HeaderAt("app.py")]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [((APP,), (NOTES,), SummaryAt()), ((NOTES,), (APP,), RowAt("app.py", 3))],
    ids=["only-before", "only-after"],
)
def test_converted_from_a_file_not_in_both_documents_recovers(
    before: tuple[FileDiff, ...], after: tuple[FileDiff, ...], expected: Position
) -> None:
    unified = build_document(diff_of(*before), (), ViewMode.UNIFIED, Scope.WHOLE)
    split = build_document(diff_of(*after), (), ViewMode.SPLIT, Scope.WHOLE)

    assert converted(RowAt("app.py", 9), unified, split) == expected


TWO_COMMENTS = build_document(
    diff_of(APP, NOTES),
    (ON_B, comment(4, "notes.md", 1, 1, ((LineKind.ADDED, "hello"),))),
    ViewMode.UNIFIED,
    Scope.WHOLE,
)


def test_next_comment_skips_hunks_and_wraps() -> None:
    assert [
        next_comment(TWO_COMMENTS, position, forward=True) for position in (SummaryAt(), CommentAt(1), CommentAt(4))
    ] == [
        CommentAt(1),
        CommentAt(4),
        CommentAt(1),
    ]


def test_previous_comment_skips_hunks_and_wraps() -> None:
    assert [
        next_comment(TWO_COMMENTS, position, forward=False) for position in (RowAt("notes.md", 0), CommentAt(1))
    ] == [
        CommentAt(1),
        CommentAt(4),
    ]


def test_next_comment_without_comments_stays_put() -> None:
    document = build_document(diff_of(APP), (), ViewMode.UNIFIED, Scope.WHOLE)

    assert next_comment(document, RowAt("app.py", 2), forward=True) == RowAt("app.py", 2)


def test_layout_of_a_row_in_another_document_refuses() -> None:
    with pytest.raises(ValueError, match="isn't in this document"):
        DOCUMENT.layout_of(RowAt("gone.py", 0))


def test_in_view_converts_the_cursor_or_leaves_it_without_a_document() -> None:
    unified = build_document(diff_of(APP), (), ViewMode.UNIFIED, Scope.WHOLE)

    assert (
        in_view(unified, RowAt("app.py", 2), (), ViewMode.SPLIT, Scope.WHOLE),
        in_view(None, RowAt("app.py", 2), (), ViewMode.SPLIT, Scope.WHOLE),
    ) == (
        RowAt("app.py", 1),
        RowAt("app.py", 2),
    )


def test_comment_row_is_the_row_or_header_a_comment_follows() -> None:
    on_a = comment(5, "app.py", 1, 1, ((LineKind.CONTEXT, "a"),))
    document = build_document(diff_of(EMPTY, APP), (ON_EMPTY, ON_B, ON_GONE, on_a), ViewMode.UNIFIED, Scope.WHOLE)

    assert [comment_row(document, number) for number in (3, 1, 5, 2, 9)] == [
        HeaderAt("empty.bin"),
        RowAt("app.py", 2),
        RowAt("app.py", 0),
        SummaryAt(),
        SummaryAt(),
    ]
    assert comment_row(None, 1) == SummaryAt()


def lines_with_added(count: int, *changed: int) -> FileDiff:
    """A file of `count` lines, numbered from 1 in the working tree, with the `changed` ones added and the rest unchanged."""
    built = []
    for number in range(1, count + 1):
        is_added = number in changed
        before = sum(1 for line in changed if line < number)
        built.append(added(number, f"new {number}") if is_added else context(number - before, number, f"line {number}"))
    return modified("long.py", *built)


def shown_numbers(layout: FileLayout) -> list[int]:
    return [primary(row).number for row in layout.rows]


def test_context_hides_the_unchanged_rows_more_than_three_from_a_change() -> None:
    (layout,) = build_document(diff_of(lines_with_added(20, 10)), (), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (shown_numbers(layout), layout.hidden) == ([7, 8, 9, 10, 11, 12, 13], ((0, 6), (7, 7)))


def test_whole_shows_every_row_and_hides_none() -> None:
    (layout,) = build_document(diff_of(lines_with_added(20, 10)), (), ViewMode.UNIFIED, Scope.WHOLE).files

    assert (shown_numbers(layout), layout.hidden) == (list(range(1, 21)), ())


@pytest.mark.parametrize(
    ("second", "hidden"),
    [(13, ((0, 2), (14, 3))), (14, ((0, 2), (15, 2))), (15, ((0, 2), (7, 2)))],
    ids=["six-apart-share-their-context", "a-lone-row-between-shows", "two-between-hide-and-a-lone-last-shows"],
)
def test_hunks_close_together_share_their_context(second: int, hidden: tuple[tuple[int, int], ...]) -> None:
    (layout,) = build_document(diff_of(lines_with_added(19, 6, second)), (), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert layout.hidden == hidden


@pytest.mark.parametrize(
    ("count", "changed"), [(7, (1, 7)), (8, (5,))], ids=["changes-at-both-ends", "a-lone-first-row-shows"]
)
def test_context_reaching_the_files_ends_hides_nothing(count: int, changed: tuple[int, ...]) -> None:
    (layout,) = build_document(diff_of(lines_with_added(count, *changed)), (), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (shown_numbers(layout), layout.hidden) == (list(range(1, count + 1)), ())


def test_a_file_of_one_unchanged_line_shows_it() -> None:
    (layout,) = build_document(diff_of(lines_with_added(1)), (), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (shown_numbers(layout), layout.hidden) == ([1], ())


def test_a_file_without_changes_hides_every_row() -> None:
    (layout,) = build_document(diff_of(lines_with_added(5)), (), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (layout.rows, layout.hidden, layout.hidden_before(0), layout.hidden_before(1)) == ((), ((0, 5),), 5, 0)


def test_a_comment_on_unchanged_lines_keeps_them_and_their_context() -> None:
    on_unchanged = comment(1, "long.py", 18, 19, ((LineKind.CONTEXT, "line 18"), (LineKind.CONTEXT, "line 19")))

    (layout,) = build_document(diff_of(lines_with_added(30, 6)), (on_unchanged,), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (shown_numbers(layout), layout.placed) == (
        [3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 18, 19, 20, 21, 22],
        ((11, (1,)),),
    )


def test_an_outdated_comment_keeps_the_row_it_is_drawn_after() -> None:
    past_the_end = comment(1, "long.py", 50, 50, ((LineKind.ADDED, "gone"),))

    (layout,) = build_document(diff_of(lines_with_added(30, 6)), (past_the_end,), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (shown_numbers(layout)[-4:], layout.placed, layout.hidden[-1]) == ([27, 28, 29, 30], ((10, (1,)),), (7, 17))


def test_an_old_side_comment_keeps_the_bases_lines_not_the_working_trees_of_the_same_number() -> None:
    file = modified("long.py", removed(1, "gone"), *(context(old, old - 1, f"line {old}") for old in range(2, 21)))
    on_old_twelve = comment(1, "long.py", 12, 12, ((LineKind.REMOVED, "twelve"),), side=Side.OLD)

    (layout,) = build_document(diff_of(file), (on_old_twelve,), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (len(layout.rows), layout.placed) == (4, ((0, (1,)),))


def test_an_old_side_comment_in_a_file_removing_nothing_goes_under_the_header_and_keeps_no_rows() -> None:
    on_removed = comment(1, "long.py", 3, 3, ((LineKind.REMOVED, "gone"),), side=Side.OLD)

    (layout,) = build_document(diff_of(lines_with_added(20, 10)), (on_removed,), ViewMode.UNIFIED, Scope.CONTEXT).files

    assert (shown_numbers(layout), layout.placed) == ([7, 8, 9, 10, 11, 12, 13], ((-1, (1,)),))


def test_a_comment_on_the_whole_file_keeps_no_rows() -> None:
    (layout,) = build_document(
        diff_of(lines_with_added(20, 10)), (file_comment(1, "long.py"),), ViewMode.UNIFIED, Scope.CONTEXT
    ).files

    assert shown_numbers(layout) == [7, 8, 9, 10, 11, 12, 13]


def test_split_view_hides_the_same_unchanged_rows() -> None:
    file = modified(
        "long.py",
        *(context(n, n, f"line {n}") for n in range(1, 10)),
        removed(10, "old"),
        added(10, "new"),
        *(context(n, n, f"line {n}") for n in range(11, 21)),
    )

    (layout,) = build_document(diff_of(file), (), ViewMode.SPLIT, Scope.CONTEXT).files

    assert (shown_numbers(layout), layout.hidden) == ([7, 8, 9, 10, 11, 12, 13], ((0, 6), (7, 7)))


def test_context_documents_have_positions_only_for_the_rows_shown() -> None:
    document = build_document(diff_of(lines_with_added(20, 10)), (), ViewMode.UNIFIED, Scope.CONTEXT)

    assert (document.scope, document.positions[-1]) == (Scope.CONTEXT, RowAt("long.py", 6))


TWO_HUNKS = diff_of(lines_with_added(21, 6, 16))
"""Rows for lines 3 to 9 and 13 to 19 show in context, hiding 10 to 12 between them."""


@pytest.mark.parametrize(
    ("line", "expected"),
    [(1, 0), (6, 3), (10, 6), (11, 6), (12, 7), (21, 13)],
    ids=["before-the-first", "shown", "just-after", "midway-takes-the-earlier", "just-before", "after-the-last"],
)
def test_converted_to_context_goes_to_the_nearest_line_shown(line: int, expected: int) -> None:
    whole = build_document(TWO_HUNKS, (), ViewMode.UNIFIED, Scope.WHOLE)
    folded = build_document(TWO_HUNKS, (), ViewMode.UNIFIED, Scope.CONTEXT)

    assert converted(RowAt("long.py", line - 1), whole, folded) == RowAt("long.py", expected)


def test_converted_to_whole_files_stays_on_the_same_line() -> None:
    whole = build_document(TWO_HUNKS, (), ViewMode.UNIFIED, Scope.WHOLE)
    folded = build_document(TWO_HUNKS, (), ViewMode.UNIFIED, Scope.CONTEXT)

    # Row 8 is line 14, the second of the second hunk's rows.
    assert converted(RowAt("long.py", 8), folded, whole) == RowAt("long.py", 13)


def test_converted_into_a_file_showing_no_rows_goes_to_its_header() -> None:
    unchanged = diff_of(lines_with_added(5))
    whole = build_document(unchanged, (), ViewMode.UNIFIED, Scope.WHOLE)
    folded = build_document(unchanged, (), ViewMode.UNIFIED, Scope.CONTEXT)

    assert converted(RowAt("long.py", 2), whole, folded) == HeaderAt("long.py")


def test_in_view_hides_rows_in_the_scope_it_switches_to() -> None:
    whole = build_document(TWO_HUNKS, (), ViewMode.UNIFIED, Scope.WHOLE)

    assert in_view(whole, RowAt("long.py", 0), (), ViewMode.UNIFIED, Scope.CONTEXT) == RowAt("long.py", 0)


def test_selected_lines_across_a_gap_take_the_lines_it_hides() -> None:
    (layout,) = build_document(TWO_HUNKS, (), ViewMode.UNIFIED, Scope.CONTEXT).files

    # Rows 6 and 7 are lines 9 and 13, either side of the gap.
    assert [line.number for line in selected_lines(layout, 6, 7)] == [9, 10, 11, 12, 13]


def test_selected_lines_of_part_of_a_split_hunk_leave_out_the_rest_of_it() -> None:
    file = modified("app.py", removed(1, "a"), removed(2, "b"), added(1, "A"), added(2, "B"))
    (layout,) = build_document(diff_of(file), (), ViewMode.SPLIT, Scope.WHOLE).files

    assert selected_lines(layout, 0, 0) == (removed(1, "a"), added(1, "A"))


def test_after_deleting_without_a_document_goes_to_the_summary() -> None:
    assert after_deleting(None, 1, ()) == SummaryAt()


def test_after_deleting_a_comment_keeping_hidden_lines_in_view_goes_to_the_nearest_line_left() -> None:
    on_ten = comment(1, "long.py", 10, 10, ((LineKind.CONTEXT, "line 10"),))
    document = build_document(TWO_HUNKS, (on_ten,), ViewMode.UNIFIED, Scope.CONTEXT)

    # Line 10 is row 7 while the comment keeps it, and hidden without it, nearest line 9, row 6.
    assert after_deleting(document, 1, (on_ten,)) == RowAt("long.py", 6)
