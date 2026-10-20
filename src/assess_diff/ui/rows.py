"""
How each row of the diff is drawn: its gutter, its text, and its colors, as counterweight chunks.

Pure and cached by the row's value, since a file's rows are the same from one frame to the next
and only the cursor's and the selection's change.
"""

from __future__ import annotations

from bisect import bisect_right
from enum import StrEnum
from functools import lru_cache
from itertools import accumulate
from itertools import groupby
from operator import itemgetter
from typing import assert_never

from counterweight.elements import Chunk
from counterweight.graphemes import grapheme_width
from counterweight.graphemes import graphemes
from counterweight.styles import Style
from counterweight.styles.utilities import text_bg
from counterweight.styles.utilities import text_color

from assess_diff.diff import Line
from assess_diff.diff import LineKind
from assess_diff.diff import SplitRow

type SyntaxLines = dict[Line, tuple[Chunk, ...]]

TAB_SIZE = 4
SPLIT_DIVIDER = "│"


class Highlight(StrEnum):
    NONE = "none"
    SELECTED = "selected"
    CURSOR = "cursor"


BACKGROUNDS = {
    LineKind.CONTEXT: Style(),
    LineKind.ADDED: text_bg("green", 950),
    LineKind.REMOVED: text_bg("red", 950),
}
MARKERS = {LineKind.CONTEXT: " ", LineKind.ADDED: "+", LineKind.REMOVED: "-"}
MARKER_COLORS = {
    LineKind.CONTEXT: text_color("slate", 500),
    LineKind.ADDED: text_color("green", 400),
    LineKind.REMOVED: text_color("red", 400),
}
HIGHLIGHTS = {
    Highlight.NONE: Style(),
    Highlight.SELECTED: text_bg("slate", 700),
    Highlight.CURSOR: text_bg("indigo", 800),
}
GUTTER = text_color("slate", 500)
ABSENT = text_bg("slate", 900)
DIVIDER = text_color("slate", 600)


def displayed(text: str) -> str:
    """A line's text as drawn: tabs expanded, since the terminal draws no tab stops, and a CRLF's CR dropped."""
    return text.removesuffix("\r").expandtabs(TAB_SIZE)


def cell_width(text: str) -> int:
    return sum(grapheme_width(grapheme) for grapheme in graphemes(text))


def fitted(chunks: tuple[Chunk, ...], width: int, style: Style) -> tuple[Chunk, ...]:
    """
    `chunks` cut or padded with spaces in `style` to exactly `width` cells,
    ending with `…` where they were cut. Neighbouring pieces in one style are joined.
    """
    if (current := chunks_width(chunks)) <= width:
        return (*chunks, Chunk(content=" " * (width - current), style=style))
    if width == 0:
        return ()
    cells = [(grapheme, chunk.style) for chunk in chunks for grapheme in graphemes(chunk.content)]
    # Cell ends only grow, so the cells that fit before the `…` are a prefix.
    kept = cells[: bisect_right(tuple(accumulate(grapheme_width(grapheme) for grapheme, _ in cells)), width - 1)]
    used = sum(grapheme_width(grapheme) for grapheme, _ in kept)
    pieces = tuple(
        Chunk(content="".join(grapheme for grapheme, _ in group), style=piece_style)
        for piece_style, group in groupby(kept, key=itemgetter(1))
    )
    return (*pieces, Chunk(content=" " * (width - 1 - used) + "…", style=style))


def number(value: int | None, digits: int) -> str:
    return " " * digits if value is None else f"{value:>{digits}}"


def text_chunks(line: Line, syntax: tuple[Chunk, ...] | None, background: Style) -> tuple[Chunk, ...]:
    """
    A line's text in its syntax colors, if it has them, under the row's background,
    which wins over a theme's own background so added and removed lines stay distinct.
    """
    if syntax is None:
        return (Chunk(content=displayed(line.text), style=background),)
    return tuple(Chunk(content=chunk.content, style=chunk.style | background.text_style) for chunk in syntax)


def chunks_width(chunks: tuple[Chunk, ...]) -> int:
    return sum(cell_width(chunk.content) for chunk in chunks)


@lru_cache(maxsize=2**14)
def unified_chunks(
    line: Line, syntax: tuple[Chunk, ...] | None, digits: int, width: int, highlight: Highlight
) -> tuple[Chunk, ...]:
    """
    A row of the unified view, padded to `width` cells so its background spans the view:
    both line numbers, the marker, and the text.
    """
    background = BACKGROUNDS[line.kind] | HIGHLIGHTS[highlight]
    gutter = f"{number(line.old, digits)} {number(line.new, digits)} "
    text = text_chunks(line, syntax, background)
    padding = " " * max(0, width - len(gutter) - 2 - chunks_width(text))
    return (
        Chunk(content=gutter, style=GUTTER | background),
        Chunk(content=f"{MARKERS[line.kind]} ", style=MARKER_COLORS[line.kind] | background),
        *text,
        Chunk(content=padding, style=background),
    )


def half(
    line: Line | None,
    syntax: tuple[Chunk, ...] | None,
    line_number: int | None,
    digits: int,
    width: int,
    highlight: Highlight,
) -> tuple[Chunk, ...]:
    """One side of a split row, exactly `width` cells: its line number on that side, marker and text, or a blank."""
    if line is None:
        return (Chunk(content=" " * width, style=ABSENT | HIGHLIGHTS[highlight]),)
    background = BACKGROUNDS[line.kind] | HIGHLIGHTS[highlight]
    gutter = f"{number(line_number, digits)} "
    return (
        Chunk(content=gutter, style=GUTTER | background),
        Chunk(content=f"{MARKERS[line.kind]} ", style=MARKER_COLORS[line.kind] | background),
        *fitted(text_chunks(line, syntax, background), max(0, width - len(gutter) - 2), background),
    )


@lru_cache(maxsize=2**14)
def split_chunks(
    row: SplitRow,
    left_syntax: tuple[Chunk, ...] | None,
    right_syntax: tuple[Chunk, ...] | None,
    digits: int,
    width: int,
    highlight: Highlight,
) -> tuple[Chunk, ...]:
    """A row of the split view, `width` cells wide: the base's side, a divider, and the working tree's side."""
    left_width = (width - 1) // 2
    right_width = width - 1 - left_width
    left, right = row.left, row.right
    return (
        *half(left, left_syntax, None if left is None else left.old, digits, left_width, highlight),
        Chunk(content=SPLIT_DIVIDER, style=DIVIDER),
        *half(right, right_syntax, None if right is None else right.new, digits, right_width, highlight),
    )


def row_chunks(
    row: Line | SplitRow, syntax: SyntaxLines, digits: int, width: int, highlight: Highlight
) -> tuple[Chunk, ...]:
    match row:
        case Line():
            return unified_chunks(row, syntax.get(row), digits, width, highlight)
        case SplitRow(left=left, right=right):
            left_syntax = None if left is None else syntax.get(left)
            right_syntax = None if right is None else syntax.get(right)
            return split_chunks(row, left_syntax, right_syntax, digits, width, highlight)
        case _ as unreachable:
            assert_never(unreachable)


def digits_for(lines: tuple[Line, ...]) -> int:
    """How many digits the gutter's line numbers take in a file: its largest number's, and at least three."""
    return max([3, *(len(str(number)) for line in lines for number in (line.old, line.new) if number is not None)])
