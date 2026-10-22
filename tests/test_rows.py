from __future__ import annotations

import pytest
from counterweight.elements import Chunk
from counterweight.styles import CellStyle
from counterweight.styles.utilities import text_color
from hypothesis import given
from hypothesis import strategies as st

from assess_diff.diff import Line
from assess_diff.diff import LineKind
from assess_diff.diff import SplitRow
from assess_diff.ui.rows import ABSENT
from assess_diff.ui.rows import BACKGROUNDS
from assess_diff.ui.rows import DIVIDER
from assess_diff.ui.rows import GUTTER
from assess_diff.ui.rows import HIGHLIGHTS
from assess_diff.ui.rows import MARKER_COLORS
from assess_diff.ui.rows import NO_TEXT
from assess_diff.ui.rows import Highlight
from assess_diff.ui.rows import Measured
from assess_diff.ui.rows import SyntaxLines
from assess_diff.ui.rows import digits_for
from assess_diff.ui.rows import displayed
from assess_diff.ui.rows import fitted
from assess_diff.ui.rows import lit
from assess_diff.ui.rows import measured_lines
from assess_diff.ui.rows import overflow
from assess_diff.ui.rows import row_chunks
from assess_diff.ui.rows import scrolled
from assess_diff.ui.rows import split_chunks
from assess_diff.ui.rows import unified_chunks
from assess_diff.ui.rows import window
from tests.builders import added
from tests.builders import context
from tests.builders import removed

RED = text_color("red", 300).text_style


@pytest.fixture(autouse=True)
def cold_row_caches() -> None:
    """
    Empties the row caches, so each test draws its rows rather than reading back another test's:
    mutmut runs each mutant in a process forked after the whole suite has warmed them.
    """
    unified_chunks.cache_clear()
    split_chunks.cache_clear()


def text_of(chunks: tuple[Chunk, ...]) -> str:
    return "".join(chunk.content for chunk in chunks)


def measured(line: Line, syntax: tuple[Chunk, ...] | None = None) -> Measured:
    return measured_lines((line,), {} if syntax is None else {line: syntax})[line]


def test_displayed_expands_tabs_and_drops_a_trailing_carriage_return() -> None:
    assert displayed("\tx\ty\r") == "    x   y"


@pytest.mark.parametrize(
    ("chunks", "width", "expected"),
    [
        ((Chunk(content="abc"),), 5, "abc  "),
        ((Chunk(content="abc"),), 3, "abc"),
        ((Chunk(content="ab"), Chunk(content="cdef")), 4, "abc…"),
        ((Chunk(content="日本語"),), 4, "日 …"),
        ((Chunk(content="abcdef"),), 1, "…"),
        ((Chunk(content="abcdef"),), 0, ""),
        ((), 0, ""),
    ],
)
def test_fitted_pads_or_cuts_to_exactly_the_width(chunks: tuple[Chunk, ...], width: int, expected: str) -> None:
    assert text_of(fitted(chunks, width, BACKGROUNDS[added(1, "").kind])) == expected


@pytest.mark.parametrize(("width", "kept", "end"), [(7, "cdef", " "), (4, "c", "…")])
def test_fitted_keeps_each_pieces_style_and_pads_or_cuts_in_the_given_style(width: int, kept: str, end: str) -> None:
    chunks = (Chunk(content="ab", style=RED), Chunk(content="cdef"))

    assert fitted(chunks, width, BACKGROUNDS[LineKind.ADDED]) == (
        Chunk(content="ab", style=RED),
        Chunk(content=kept),
        Chunk(content=end, style=BACKGROUNDS[LineKind.ADDED]),
    )


def test_unified_row_has_both_numbers_a_marker_and_pads_to_the_width() -> None:
    line = context(7, 8, "text")

    chunks = unified_chunks(line, measured(line), 3, 20, 0, Highlight.NONE)

    assert text_of(chunks) == "  7   8   text      "


def test_unified_row_of_an_added_line_has_only_the_new_number() -> None:
    line = added(12, "x")

    assert text_of(unified_chunks(line, measured(line), 3, 11, 0, Highlight.NONE)) == "     12 + x"


def test_unified_row_lays_syntax_colors_under_the_row_background() -> None:
    line = removed(3, "x")

    chunks = unified_chunks(line, measured(line, (Chunk(content="x", style=RED),)), 3, 11, 0, Highlight.CURSOR)

    background = (BACKGROUNDS[line.kind] | HIGHLIGHTS[Highlight.CURSOR]).text_style
    assert chunks[2] == Chunk(content="x", style=RED | background)


def test_split_row_puts_old_numbers_left_and_new_numbers_right() -> None:
    line = context(4, 6, "same")

    assert text_of(split_chunks(SplitRow(line, line), measured(line), measured(line), 3, 31, 0, Highlight.NONE)) == (
        "  4   same     │  6   same     "
    )


def test_split_row_styles_each_sides_gutter_marker_and_text_and_fits_the_text_to_its_half() -> None:
    old, new = removed(4, "ab"), added(6, "cdefgh")
    old_background = BACKGROUNDS[old.kind] | HIGHLIGHTS[Highlight.CURSOR]
    new_background = BACKGROUNDS[new.kind] | HIGHLIGHTS[Highlight.CURSOR]

    chunks = split_chunks(SplitRow(old, new), measured(old), measured(new), 3, 21, 0, Highlight.CURSOR)

    assert chunks == (
        Chunk(content="  4 ", style=GUTTER | old_background),
        Chunk(content="- ", style=MARKER_COLORS[old.kind] | old_background),
        Chunk(content="ab", style=old_background),
        Chunk(content="  ", style=old_background),
        Chunk(content="│", style=DIVIDER),
        Chunk(content="  6 ", style=GUTTER | new_background),
        Chunk(content="+ ", style=MARKER_COLORS[new.kind] | new_background),
        Chunk(content="cde", style=new_background),
        Chunk(content="…", style=new_background),
    )


def test_split_row_exactly_as_wide_as_its_gutters_and_markers_leaves_no_cells_for_text() -> None:
    line = context(4, 6, "")

    assert text_of(split_chunks(SplitRow(line, line), measured(line), measured(line), 3, 13, 0, Highlight.NONE)) == (
        "  4   │  6   "
    )


def test_split_row_with_one_side_blank_fills_it() -> None:
    line = added(2, "new")

    chunks = split_chunks(SplitRow(None, line), NO_TEXT, measured(line), 3, 21, 0, Highlight.SELECTED)

    assert chunks[0] == Chunk(content=" " * 10, style=ABSENT | HIGHLIGHTS[Highlight.SELECTED])


def test_row_chunks_looks_up_each_lines_syntax() -> None:
    old, new = removed(1, "a"), added(1, "b")
    syntax: SyntaxLines = {old: (Chunk(content="a", style=RED),), new: (Chunk(content="b", style=RED),)}

    texts = measured_lines((old, new), syntax)

    unified = row_chunks(new, texts, 3, 11, 0, Highlight.NONE)
    split = row_chunks(SplitRow(old, new), texts, 3, 21, 0, Highlight.NONE)

    assert unified[2].style == RED | BACKGROUNDS[new.kind].text_style
    assert [chunk.style for chunk in split if chunk.content in {"a", "b"}] == [
        RED | BACKGROUNDS[old.kind].text_style,
        RED | BACKGROUNDS[new.kind].text_style,
    ]


def test_digits_are_the_largest_numbers_and_at_least_three() -> None:
    assert (digits_for((context(1, 1, ""),)), digits_for((removed(12345, ""), added(2, ""))), digits_for(())) == (
        3,
        5,
        3,
    )


def test_unstyled_text_takes_the_row_background() -> None:
    line = added(1, "plain")

    chunks = unified_chunks(line, measured(line), 3, 15, 0, Highlight.NONE)

    assert chunks[2] == Chunk(content="plain", style=BACKGROUNDS[line.kind])
    assert chunks[2].style != CellStyle()


@pytest.mark.parametrize(
    ("chunks", "offset", "expected"),
    [
        ((Chunk(content="abcdef"),), 0, "abcdef"),
        ((Chunk(content="ab"), Chunk(content="cdef")), 2, "…def"),
        ((Chunk(content="日本語"),), 1, "…本語"),
        ((Chunk(content="日本語"),), 2, "… 語"),
        ((Chunk(content="ab"),), 5, "…"),
        ((), 3, ""),
    ],
    ids=["unscrolled", "across-chunks", "at-a-wide-grapheme", "through-a-wide-grapheme", "past-the-end", "empty"],
)
def test_scrolled_cuts_the_first_cells_and_marks_the_cut(chunks: tuple[Chunk, ...], offset: int, expected: str) -> None:
    assert text_of(scrolled(chunks, offset, BACKGROUNDS[LineKind.ADDED])) == expected


def test_scrolled_keeps_each_pieces_style_and_marks_the_cut_in_the_given_style() -> None:
    chunks = (Chunk(content="ab", style=RED), Chunk(content="cdef"))

    assert scrolled(chunks, 1, BACKGROUNDS[LineKind.ADDED]) == (
        Chunk(content="…", style=BACKGROUNDS[LineKind.ADDED]),
        Chunk(content="cdef"),
    )


def test_unified_row_scrolls_its_text_and_keeps_its_gutter() -> None:
    line = context(7, 8, "abcdefghij")

    chunks = unified_chunks(line, measured(line), 3, 15, 3, Highlight.NONE)

    assert text_of(chunks) == "  7   8   …efg…"


def test_split_row_scrolls_both_sides_alike() -> None:
    old, new = removed(4, "abcdefgh"), added(6, "ABCDEFGH")

    chunks = split_chunks(SplitRow(old, new), measured(old), measured(new), 3, 25, 2, Highlight.NONE)

    assert text_of(chunks) == "  4 - …defgh│  6 + …DEFGH"


@pytest.mark.parametrize(
    ("rows", "width", "expected"),
    [
        ((context(1, 1, "x" * 30), context(2, 2, "short")), 20, 20),
        ((SplitRow(None, added(1, "y" * 12)), SplitRow(removed(1, "z" * 9), None)), 21, 8),
        ((context(1, 1, "fits"),), 20, 0),
        ((), 20, 0),
    ],
    ids=["unified", "split", "fits", "empty"],
)
def test_overflow_is_how_far_the_widest_text_runs_past_what_shows(
    rows: tuple[Line | SplitRow, ...], width: int, expected: int
) -> None:
    assert overflow(rows, 3, width) == expected


PIECES = st.lists(
    st.builds(
        lambda content, style: Chunk(content=content, style=style),
        # Wide graphemes, a combining mark, and empty chunks, around which the cuts are hardest to place.
        st.lists(st.sampled_from(["a", "b", " ", "日", "é", "e\u0301"]), max_size=6).map("".join),
        st.sampled_from([CellStyle(), RED]),
    ),
    max_size=8,
).map(tuple)


@given(PIECES, st.integers(0, 30), st.integers(0, 30))
def test_window_draws_what_scrolling_and_fitting_the_whole_text_draws(
    chunks: tuple[Chunk, ...], offset: int, width: int
) -> None:
    background = BACKGROUNDS[LineKind.ADDED]
    nonempty = tuple(chunk for chunk in chunks if chunk.content)

    assert window(Measured.of(chunks), offset, width, background) == fitted(
        scrolled(nonempty, offset, background), width, background
    )


def test_measured_text_drops_empty_chunks_and_knows_where_each_starts() -> None:
    text = Measured.of((Chunk(content="ab"), Chunk(content=""), Chunk(content="日")))

    assert (text.chunks, text.starts) == ((Chunk(content="ab"), Chunk(content="日")), (0, 2, 4))


def test_lit_lays_the_highlight_over_each_chunks_background() -> None:
    chunks = (Chunk(content="ab", style=RED | BACKGROUNDS[LineKind.ADDED].text_style),)

    assert lit(chunks, Highlight.SELECTED) == (
        Chunk(content="ab", style=RED | HIGHLIGHTS[Highlight.SELECTED].text_style),
    )


def test_lit_leaves_unhighlighted_chunks_be() -> None:
    chunks = (Chunk(content="ab", style=RED),)

    assert lit(chunks, Highlight.NONE) is chunks
