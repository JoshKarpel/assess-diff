from __future__ import annotations

import pytest
from counterweight.elements import Chunk
from counterweight.styles import Style

from assess_diff.diff import Line
from assess_diff.navigation import RowAt
from assess_diff.search import Match
from assess_diff.ui.text_search import MATCH_STYLE
from assess_diff.ui.text_search import MOST_SHOWN
from assess_diff.ui.text_search import excerpt
from assess_diff.ui.text_search import listed_from
from assess_diff.ui.text_search import place
from assess_diff.ui.text_search import shown_for
from assess_diff.ui.text_search import tally
from tests.builders import added
from tests.builders import context
from tests.builders import removed


def matched(line: Line, query: str, path: str = "src/app.py") -> Match:
    start = line.text.index(query)
    return Match(at=RowAt(path, 3), line=line, start=start, stop=start + len(query))


def text_of(chunks: tuple[Chunk, ...]) -> str:
    return "".join(chunk.content for chunk in chunks)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (added(42, "x = 1"), "src/app.py:42"),
        (removed(17, "x = 1"), "src/app.py:17"),
        (context(5, 9, "x"), "src/app.py:9"),
    ],
    ids=["added", "removed", "context"],
)
def test_place_is_the_file_and_the_lines_number_on_its_own_side(line: Line, expected: str) -> None:
    assert place(matched(line, "x")) == expected


def test_excerpt_of_a_short_line_drops_its_indentation_marks_the_match_and_pads_to_the_width() -> None:
    assert excerpt(matched(added(3, "\t    total = compute(items)\r"), "compute"), 30) == (
        Chunk(content="total = "),
        Chunk(content="compute", style=MATCH_STYLE),
        Chunk(content="(items)"),
        Chunk(content=" " * 8, style=Style()),
    )


def test_excerpt_of_a_long_line_keeps_a_third_of_the_width_before_the_match() -> None:
    line = added(3, " + ".join(f"term{index}" for index in range(40)) + " + needle + tail" * 10)
    match = matched(line, "needle")

    shown = excerpt(match, 30)

    assert text_of(shown) == "…term39 + needle + tail + nee…"
    assert Chunk(content="needle", style=MATCH_STYLE) in shown


def test_excerpt_of_a_match_near_the_end_of_a_long_line_shows_the_end() -> None:
    line = added(3, "x" * 60 + "needle")

    shown = text_of(excerpt(matched(line, "needle"), 20))

    assert shown == "…" + "x" * 13 + "needle"


def test_excerpt_marks_where_it_cuts_in_the_lines_own_style() -> None:
    line = added(3, "x" * 60 + "needle")

    assert excerpt(matched(line, "needle"), 20) == (
        Chunk(content="…", style=Style()),
        Chunk(content="x" * 13),
        Chunk(content="needle", style=MATCH_STYLE),
        Chunk(content="", style=Style()),
    )


def test_excerpt_expands_tabs_inside_the_line() -> None:
    assert text_of(excerpt(matched(added(3, "a\tneedle"), "needle"), 12)) == "a   needle  "


@pytest.mark.parametrize(
    ("highlighted", "count", "expected"),
    [(0, 50, 0), (2, 50, 0), (20, 50, 18), (49, 50, 45), (3, 4, 0)],
    ids=["first", "near-the-start", "middle", "last", "fewer-than-shown"],
)
def test_listed_from_keeps_the_highlighted_match_in_the_middle_but_near_either_end(
    highlighted: int, count: int, expected: int
) -> None:
    # An odd number listed, so the middle is a whole number of matches in only by rounding down.
    assert listed_from(highlighted, count, 5) == expected


@pytest.mark.parametrize(
    ("screen_height", "expected"), [(10, 1), (24, 4), (32, 8), (100, MOST_SHOWN)], ids=["tiny", "24", "32", "tall"]
)
def test_shown_for_keeps_the_dialog_below_the_middle_of_the_screen(screen_height: int, expected: int) -> None:
    assert shown_for(screen_height) == expected


@pytest.mark.parametrize(
    ("query", "count", "index", "expected"),
    [
        ("", 0, None, "Type text to find, such as a name"),
        ("nope", 0, None, "No line holds “nope”"),
        ("one", 1, 0, "1 of 1 line"),
        ("x", 12, 4, "5 of 12 lines"),
    ],
    ids=["nothing-typed", "nothing-found", "one", "several"],
)
def test_tally_says_which_match_is_highlighted_of_how_many_or_why_none_is(
    query: str, count: int, index: int | None, expected: str
) -> None:
    found = tuple(matched(added(line, "x"), "x") for line in range(count))

    assert tally(query, found, index) == expected
