"""
How each row of the diff is drawn: its gutter, its text, and its colors, as counterweight chunks.

Pure and cached by the row's value, since a file's rows are the same from one frame to the next
and only the cursor's and the selection's change.
Scrolling sideways changes every row, so each line's text is measured once, as a `Measured`,
and a row only cuts the window that shows out of it.
"""

from __future__ import annotations

from bisect import bisect_left
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from itertools import accumulate
from itertools import chain
from itertools import groupby
from operator import itemgetter
from typing import assert_never

from counterweight.elements import Chunk
from counterweight.styles import CellStyle
from counterweight.styles import Style
from counterweight.styles.utilities import text_bg
from counterweight.styles.utilities import text_color
from counterweight.text import grapheme_width
from counterweight.text import graphemes

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


type Cells = list[tuple[str, CellStyle]]


def cells_of(chunks: tuple[Chunk, ...]) -> Cells:
    """`chunks` as their graphemes, each with its chunk's style, to cut between any two."""
    return [(grapheme, chunk.style) for chunk in chunks for grapheme in graphemes(chunk.content)]


def joined(cells: Cells) -> tuple[Chunk, ...]:
    """`cells` as chunks again, neighbouring cells in one style joined."""
    return tuple(
        Chunk(content="".join(grapheme for grapheme, _ in group), style=piece_style)
        for piece_style, group in groupby(cells, key=itemgetter(1))
    )


def fitted(chunks: tuple[Chunk, ...], width: int, style: Style) -> tuple[Chunk, ...]:
    """
    `chunks` cut or padded with spaces in `style` to exactly `width` cells,
    ending with `…` where they were cut. Neighbouring pieces in one style are joined.
    """
    if (current := chunks_width(chunks)) <= width:
        return (*chunks, Chunk(content=" " * (width - current), style=style))
    if width == 0:
        return ()
    cells = cells_of(chunks)
    # Cell ends only grow, so the cells that fit before the `…` are a prefix.
    kept = cells[: bisect_right(tuple(accumulate(grapheme_width(grapheme) for grapheme, _ in cells)), width - 1)]
    used = sum(grapheme_width(grapheme) for grapheme, _ in kept)
    return (*joined(kept), Chunk(content=" " * (width - 1 - used) + "…", style=style))


def scrolled(chunks: tuple[Chunk, ...], offset: int, style: Style) -> tuple[Chunk, ...]:
    """
    `chunks` with their first `offset` cells scrolled out of view to the left,
    starting with `…` in `style` where they were cut, even when all of them were.
    A wide grapheme the cut runs through is dropped, and spaces in `style` take its place.
    """
    if offset == 0 or not (cells := cells_of(chunks)):
        return chunks
    starts = tuple(accumulate((grapheme_width(grapheme) for grapheme, _ in cells), initial=0))
    # The `…` takes the cell at `offset`, so text shows from the cell after it.
    # Cell starts only grow, so the cells starting there or later are a suffix.
    first = bisect_left(starts, offset + 1, hi=len(cells))
    gap = starts[first] - (offset + 1) if first < len(cells) else 0
    return (Chunk(content="…" + " " * gap, style=style), *joined(cells[first:]))


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


@dataclass(frozen=True, slots=True, eq=False)
class Measured:
    """
    A line's text as chunks in its row's colors, with the cell each one starts at,
    so cutting it to whichever window shows measures only the chunks in that window.
    Compared and hashed by identity, so the row caches key on it cheaply:
    a file's are made once for each theme, and keep their identity while it's drawn.
    """

    chunks: tuple[Chunk, ...]
    starts: tuple[int, ...]
    """The cell each chunk starts at, and then the cell after the last chunk: the text's width."""

    @classmethod
    def of(cls, chunks: tuple[Chunk, ...]) -> Measured:
        # An empty chunk takes no cells: left in, it could be all the window finds before the text it cuts.
        kept = tuple(chunk for chunk in chunks if chunk.content)
        return cls(chunks=kept, starts=tuple(accumulate((cell_width(chunk.content) for chunk in kept), initial=0)))


type Texts = dict[Line, Measured]

NO_TEXT = Measured.of(())
"""The text of a split row's side that has no line."""


def measured_lines(lines: tuple[Line, ...], syntax: SyntaxLines) -> Texts:
    """Each of a file's `lines` in its syntax colors, if it has them, under its kind's background, measured."""
    return {line: Measured.of(text_chunks(line, syntax.get(line), BACKGROUNDS[line.kind])) for line in lines}


def window(text: Measured, offset: int, width: int, style: Style) -> tuple[Chunk, ...]:
    """
    `text` scrolled `offset` cells to the left and cut or padded to exactly `width` cells in `style`,
    as `fitted(scrolled(text.chunks, offset, style), width, style)` draws it,
    from only the chunks that show, the one holding the cell the `…` covers, and one past the right edge.
    """
    chunks, starts = text.chunks, text.starts
    # The chunk holding the cell before the window, so `scrolled` marks the cut, or with no cut, the first.
    first = max(0, min(bisect_left(starts, offset), len(chunks)) - 1)
    # Every chunk starting before the cell past the window's last, so `fitted` sees whether the text runs on.
    stop = bisect_left(starts, offset + width + 1, lo=first, hi=len(chunks))
    return fitted(scrolled(chunks[first:stop], offset - starts[first], style), width, style)


def lit(chunks: tuple[Chunk, ...], highlight: Highlight) -> tuple[Chunk, ...]:
    """`chunks` under the cursor's or the selection's background, which wins over the row's."""
    if highlight is Highlight.NONE:
        return chunks
    over = HIGHLIGHTS[highlight].text_style
    return tuple(Chunk(content=chunk.content, style=chunk.style | over) for chunk in chunks)


def unified_room(digits: int, width: int) -> int:
    """How many cells a unified row `width` cells wide leaves for its text, after both numbers and the marker."""
    return max(0, width - (2 * digits + 2) - 2)


def split_widths(width: int) -> tuple[int, int]:
    """How many cells each side of a split row `width` cells wide takes, either side of the divider."""
    left_width = (width - 1) // 2
    return left_width, width - 1 - left_width


def half_room(digits: int, width: int) -> int:
    """How many cells one side of a split row, `width` cells wide, leaves for its text, after its number and marker."""
    return max(0, width - (digits + 1) - 2)


@lru_cache(maxsize=2**14)
def unified_chunks(
    line: Line, text: Measured, digits: int, width: int, offset: int, highlight: Highlight
) -> tuple[Chunk, ...]:
    """
    A row of the unified view, exactly `width` cells so its background spans the view:
    both line numbers, the marker, and the line's `text`, scrolled `offset` cells to the left.
    """
    background = BACKGROUNDS[line.kind] | HIGHLIGHTS[highlight]
    gutter = f"{number(line.old, digits)} {number(line.new, digits)} "
    return (
        Chunk(content=gutter, style=GUTTER | background),
        Chunk(content=f"{MARKERS[line.kind]} ", style=MARKER_COLORS[line.kind] | background),
        *lit(window(text, offset, unified_room(digits, width), background), highlight),
    )


def half(
    line: Line | None,
    text: Measured,
    line_number: int | None,
    digits: int,
    width: int,
    offset: int,
    highlight: Highlight,
) -> tuple[Chunk, ...]:
    """
    One side of a split row, exactly `width` cells: its line number on that side, marker and `text`,
    scrolled `offset` cells to the left, or a blank.
    """
    if line is None:
        return (Chunk(content=" " * width, style=ABSENT | HIGHLIGHTS[highlight]),)
    background = BACKGROUNDS[line.kind] | HIGHLIGHTS[highlight]
    return (
        Chunk(content=f"{number(line_number, digits)} ", style=GUTTER | background),
        Chunk(content=f"{MARKERS[line.kind]} ", style=MARKER_COLORS[line.kind] | background),
        *lit(window(text, offset, half_room(digits, width), background), highlight),
    )


@lru_cache(maxsize=2**14)
def split_chunks(
    row: SplitRow,
    left_text: Measured,
    right_text: Measured,
    digits: int,
    width: int,
    offset: int,
    highlight: Highlight,
) -> tuple[Chunk, ...]:
    """
    A row of the split view, `width` cells wide: the base's side, a divider, and the working tree's side,
    both scrolled `offset` cells to the left, so lines across from each other stay lined up.
    """
    left_width, right_width = split_widths(width)
    left, right = row.left, row.right
    return (
        *half(left, left_text, None if left is None else left.old, digits, left_width, offset, highlight),
        Chunk(content=SPLIT_DIVIDER, style=DIVIDER),
        *half(right, right_text, None if right is None else right.new, digits, right_width, offset, highlight),
    )


def row_chunks(
    row: Line | SplitRow, texts: Texts, digits: int, width: int, offset: int, highlight: Highlight
) -> tuple[Chunk, ...]:
    """A row of either view, from its lines' texts in `texts`, which holds every line of the row's file."""
    match row:
        case Line():
            return unified_chunks(row, texts[row], digits, width, offset, highlight)
        case SplitRow(left=left, right=right):
            left_text = NO_TEXT if left is None else texts[left]
            right_text = NO_TEXT if right is None else texts[right]
            return split_chunks(row, left_text, right_text, digits, width, offset, highlight)
        case _ as unreachable:
            assert_never(unreachable)


def overflow(rows: tuple[Line | SplitRow, ...], digits: int, width: int) -> int:
    """
    How far the text of the widest of a file's `rows`, drawn `width` cells wide, runs past what shows of it:
    the furthest the file scrolls left, which brings that text's end to the right edge.
    """
    unified = unified_room(digits, width)
    left_width, right_width = split_widths(width)
    rooms = (half_room(digits, left_width), half_room(digits, right_width))

    def past(row: Line | SplitRow) -> Iterator[int]:
        match row:
            case Line():
                yield cell_width(displayed(row.text)) - unified
            case SplitRow():
                for line, room in zip((row.left, row.right), rooms, strict=True):
                    if line is not None:
                        yield cell_width(displayed(line.text)) - room
            case _ as unreachable:
                assert_never(unreachable)

    return max(chain((0,), (distance for row in rows for distance in past(row))))


def digits_for(lines: tuple[Line, ...]) -> int:
    """How many digits the gutter's line numbers take in a file: its largest number's, and at least three."""
    return max([3, *(len(str(number)) for line in lines for number in (line.old, line.new) if number is not None)])
