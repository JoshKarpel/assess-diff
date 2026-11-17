"""
Finding text in the diff: the lines that hold what's typed in the search dialog, in display order,
each at the row the cursor goes to for it.

It reads a `Document` rather than the diff, so it finds lines as the view shows them:
a split row holding two matching lines is found twice, once for each line, both at that row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from counterweight.headless import Highlight

from assess_diff.diff import Line
from assess_diff.diff import Side
from assess_diff.navigation import Document
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import row_lines


@dataclass(frozen=True, slots=True)
class Match:
    """A line holding the query: the row showing it, the line, and where in its text the first occurrence is."""

    at: RowAt
    line: Line
    start: int
    stop: int


def pattern_for(query: str) -> re.Pattern[str]:
    """
    `query` as literal text, matched ignoring case unless it has a capital letter, as ripgrep's `--smart-case` does,
    so a name typed in lowercase finds it however it's written, and one typed with its capitals finds only that.
    """
    flags = re.NOFLAG if any(character.isupper() for character in query) else re.IGNORECASE
    return re.compile(re.escape(query), flags)


def find(document: Document, query: str) -> tuple[Match, ...]:
    """Every line of `document` that holds `query`, in display order; none while nothing is typed."""
    if not query:
        return ()
    pattern = pattern_for(query)
    return tuple(
        Match(at=RowAt(layout.file.path, index), line=line, start=found.start(), stop=found.end())
        for layout in document.files
        for index, row in enumerate(layout.rows)
        for line in row_lines(row)
        if (found := pattern.search(line.text)) is not None
    )


type MatchKey = tuple[str, Side, int]


def match_key(match: Match) -> MatchKey:
    """
    What keeps a chosen match chosen while the diff changes under the dialog: its file and its line, by side and number,
    so matches coming and going before it don't move the highlight onto another line.
    A line's new number shifts when lines above it are added or removed, and the highlight then stays at its index.
    """
    return match.at.path, match.line.side, match.line.number


def highlighted(document: Document, found: tuple[Match, ...], origin: Position, chosen: Highlight | None) -> int | None:
    """
    Which of `found` the dialog highlights, and the diff previews: the one `chosen` with ↑↓,
    or until one is, the first at or after `origin`, where the cursor was, wrapping to the first;
    `None` when nothing is found.
    """
    if not found:
        return None
    if chosen is not None:
        return chosen.index_in(found, match_key)
    at = document.indexes[origin]
    return next((index for index, match in enumerate(found) if document.indexes[match.at] >= at), 0)
