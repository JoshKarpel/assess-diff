"""
Pills, as zellij's status bar draws them: what the header says and the keys the footers list,
each group on its own background, padded by a cell, a bare space apart.
Every footer draws its keys this way, the main screen's and each dialog's, so they read as one thing.
"""

from __future__ import annotations

from collections.abc import Iterable

from counterweight.elements import Chunk
from counterweight.styles import Style
from counterweight.styles.utilities import text_bg
from counterweight.styles.utilities import text_color

NO_BREAK_SPACE = "\N{NO-BREAK SPACE}"

ON_SCREEN = text_bg("slate", 900)
ON_DIALOG = text_bg("slate", 700)
"""A pill inside a dialog, which is itself on the screen's pill color, so a pill there is a shade lighter."""


def pills(groups: Iterable[tuple[Chunk, ...]], background: Style = ON_SCREEN) -> tuple[Chunk, ...]:
    """
    `groups` as pills on `background`, a bare space apart.
    Their own spaces are made no-break, so they wrap only between pills.
    """
    on_pill = background.text_style
    edge = Chunk(content=NO_BREAK_SPACE, style=on_pill)
    return tuple(
        chunk
        for index, group in enumerate(groups)
        for chunk in (
            *((Chunk(content=" "),) if index else ()),
            edge,
            *(
                Chunk(content=chunk.content.replace(" ", NO_BREAK_SPACE), style=chunk.style | on_pill)
                for chunk in group
            ),
            edge,
        )
    )


def key_pills(hints: Iterable[str], background: Style = ON_SCREEN) -> tuple[Chunk, ...]:
    """Key hints, such as `enter open`, as pills on `background`."""
    return pills(((Chunk(content=hint, style=text_color("slate", 400)),) for hint in hints), background)
