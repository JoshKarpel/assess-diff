from __future__ import annotations

import pytest
from counterweight.elements import Chunk
from counterweight.styles import CellStyle
from counterweight.styles.utilities import text_color

from assess_diff.diff import SplitRow
from assess_diff.ui.rows import ABSENT
from assess_diff.ui.rows import BACKGROUNDS
from assess_diff.ui.rows import HIGHLIGHTS
from assess_diff.ui.rows import Highlight
from assess_diff.ui.rows import SyntaxLines
from assess_diff.ui.rows import digits_for
from assess_diff.ui.rows import displayed
from assess_diff.ui.rows import fitted
from assess_diff.ui.rows import row_chunks
from assess_diff.ui.rows import split_chunks
from assess_diff.ui.rows import unified_chunks
from tests.builders import added
from tests.builders import context
from tests.builders import removed

RED = text_color("red", 300).text_style


def text_of(chunks: tuple[Chunk, ...]) -> str:
    return "".join(chunk.content for chunk in chunks)


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
    ],
)
def test_fitted_pads_or_cuts_to_exactly_the_width(chunks: tuple[Chunk, ...], width: int, expected: str) -> None:
    assert text_of(fitted(chunks, width, BACKGROUNDS[added(1, "").kind])) == expected


def test_fitted_keeps_each_kept_pieces_style() -> None:
    chunks = (Chunk(content="ab", style=RED), Chunk(content="cdef"))

    assert fitted(chunks, 4, BACKGROUNDS[added(1, "").kind])[:2] == (
        Chunk(content="ab", style=RED),
        Chunk(content="c"),
    )


def test_unified_row_has_both_numbers_a_marker_and_pads_to_the_width() -> None:
    chunks = unified_chunks(context(7, 8, "text"), None, 3, 20, Highlight.NONE)

    assert text_of(chunks) == "  7   8   text      "


def test_unified_row_of_an_added_line_has_only_the_new_number() -> None:
    assert text_of(unified_chunks(added(12, "x"), None, 3, 0, Highlight.NONE)) == "     12 + x"


def test_unified_row_lays_syntax_colors_under_the_row_background() -> None:
    line = removed(3, "x")

    chunks = unified_chunks(line, (Chunk(content="x", style=RED),), 3, 0, Highlight.CURSOR)

    background = (BACKGROUNDS[line.kind] | HIGHLIGHTS[Highlight.CURSOR]).text_style
    assert chunks[2] == Chunk(content="x", style=RED | background)


def test_split_row_puts_old_numbers_left_and_new_numbers_right() -> None:
    line = context(4, 6, "same")

    assert text_of(split_chunks(SplitRow(line, line), None, None, 3, 31, Highlight.NONE)) == (
        "  4   same     │  6   same     "
    )


def test_split_row_with_one_side_blank_fills_it() -> None:
    chunks = split_chunks(SplitRow(None, added(2, "new")), None, None, 3, 21, Highlight.SELECTED)

    assert chunks[0] == Chunk(content=" " * 10, style=ABSENT | HIGHLIGHTS[Highlight.SELECTED])


def test_row_chunks_looks_up_each_lines_syntax() -> None:
    old, new = removed(1, "a"), added(1, "b")
    syntax: SyntaxLines = {old: (Chunk(content="a", style=RED),), new: (Chunk(content="b", style=RED),)}

    unified = row_chunks(new, syntax, 3, 0, Highlight.NONE)
    split = row_chunks(SplitRow(old, new), syntax, 3, 21, Highlight.NONE)

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

    chunks = unified_chunks(line, None, 3, 0, Highlight.NONE)

    assert chunks[2] == Chunk(content="plain", style=BACKGROUNDS[line.kind])
    assert chunks[2].style != CellStyle()
