from __future__ import annotations

import pytest
from counterweight.headless import Highlight

from assess_diff.navigation import CommentAt
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import Scope
from assess_diff.navigation import SummaryAt
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.search import Match
from assess_diff.search import find
from assess_diff.search import highlighted
from assess_diff.search import match_key
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import diff_of
from tests.builders import modified
from tests.builders import removed

RENAMED_OLD = removed(2, "    total = compute_total(items)")
RENAMED_NEW = added(2, "    total = compute_sum(items)")
APP = modified(
    "app.py",
    context(1, 1, "def report(items):"),
    RENAMED_OLD,
    RENAMED_NEW,
    context(3, 3, "    return Total(total)  # the total"),
)
CALLER = modified("caller.py", context(7, 7, "print(report(items))"), added(8, "log(compute_sum)", hunk=1))
UNIFIED = build_document(diff_of(APP, CALLER), (), ViewMode.UNIFIED, Scope.WHOLE)


def test_find_lists_every_line_holding_the_query_in_display_order() -> None:
    assert find(UNIFIED, "compute") == (
        Match(at=RowAt("app.py", 1), line=RENAMED_OLD, start=12, stop=19),
        Match(at=RowAt("app.py", 2), line=RENAMED_NEW, start=12, stop=19),
        Match(at=RowAt("caller.py", 1), line=CALLER.lines[1], start=4, stop=11),
    )


def test_find_marks_the_first_occurrence_in_a_line() -> None:
    (match,) = find(UNIFIED, "return")

    assert (match.start, match.stop) == (4, 10)


def test_find_finds_nothing_while_nothing_is_typed() -> None:
    assert find(UNIFIED, "") == ()


@pytest.mark.parametrize(
    ("query", "rows"),
    [
        ("total", [RowAt("app.py", 1), RowAt("app.py", 2), RowAt("app.py", 3)]),
        ("Total", [RowAt("app.py", 3)]),
        ("TOTAL", []),
    ],
    ids=["lowercase-ignores-case", "capital-matches-case", "capitals-match-case"],
)
def test_find_ignores_case_unless_the_query_has_a_capital(query: str, rows: list[RowAt]) -> None:
    assert [match.at for match in find(UNIFIED, query)] == rows


@pytest.mark.parametrize(("query", "count"), [("report", 1), ("Report", 1), ("REPORT", 0)])
def test_find_matches_a_name_written_with_capitals_only_as_typed_once_the_query_has_one(query: str, count: int) -> None:
    document = build_document(
        diff_of(modified("views.py", added(1, "class Report:"))), (), ViewMode.UNIFIED, Scope.WHOLE
    )

    assert len(find(document, query)) == count


def test_find_matches_the_query_as_literal_text() -> None:
    assert [match.at for match in find(UNIFIED, "report(")] == [RowAt("app.py", 0), RowAt("caller.py", 0)]
    assert find(UNIFIED, "items.") == ()


def test_find_in_the_split_view_finds_each_line_of_a_row_at_that_row() -> None:
    split = build_document(diff_of(APP), (), ViewMode.SPLIT, Scope.WHOLE)

    assert [(match.at, match.line) for match in find(split, "compute_")] == [
        (RowAt("app.py", 1), RENAMED_OLD),
        (RowAt("app.py", 1), RENAMED_NEW),
    ]


FOUND = find(UNIFIED, "compute")
COMMENTED = build_document(diff_of(APP, CALLER), (comment(1, "app.py", 2, 2, ()),), ViewMode.UNIFIED, Scope.WHOLE)


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (SummaryAt(), 0),
        (RowAt("app.py", 1), 0),
        (RowAt("app.py", 2), 1),
        (HeaderAt("caller.py"), 2),
        (RowAt("caller.py", 1), 2),
    ],
    ids=["before-all", "on-the-first", "on-the-second", "between", "on-the-last"],
)
def test_highlighted_is_the_first_match_at_or_after_the_cursor(origin: Position, expected: int) -> None:
    assert highlighted(UNIFIED, FOUND, origin, None) == expected


def test_highlighted_wraps_to_the_first_match_past_the_last() -> None:
    assert highlighted(UNIFIED, find(UNIFIED, "items)"), RowAt("caller.py", 1), None) == 0


def test_highlighted_counts_from_a_comment_where_it_is_drawn() -> None:
    assert highlighted(COMMENTED, find(COMMENTED, "compute"), CommentAt(1), None) == 2


@pytest.mark.parametrize(("chosen", "expected"), [(1, 1), (2, 2), (5, 2)], ids=["chosen", "last", "past-the-end"])
def test_highlighted_is_the_one_chosen_within_those_found(chosen: int, expected: int) -> None:
    assert highlighted(UNIFIED, FOUND, RowAt("caller.py", 1), Highlight(index=chosen)) == expected


def test_highlighted_stays_on_the_line_chosen_as_a_match_appears_before_it() -> None:
    chosen = Highlight(key=match_key(FOUND[1]), index=1)
    document = build_document(
        diff_of(modified("aaa.py", added(1, "compute()")), APP, CALLER), (), ViewMode.UNIFIED, Scope.WHOLE
    )

    # The new file's match comes first, so the line chosen, the second found before, is now the third.
    assert highlighted(document, find(document, "compute"), SummaryAt(), chosen) == 2


def test_match_key_tells_apart_the_lines_of_a_split_row() -> None:
    split = build_document(diff_of(APP), (), ViewMode.SPLIT, Scope.WHOLE)
    old, new = find(split, "compute_")

    assert match_key(old) != match_key(new)


@pytest.mark.parametrize("chosen", [None, Highlight(index=3)])
def test_highlighted_is_none_when_nothing_is_found(chosen: Highlight | None) -> None:
    assert highlighted(UNIFIED, (), SummaryAt(), chosen) is None
