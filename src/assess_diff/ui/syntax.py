"""
Syntax colors for a file's lines, from counterweight's Pygments highlighting.

The working tree's lines are highlighted as one document, so a construct that spans lines (a docstring)
colors right; each hunk's removed lines are highlighted together, since the base's whole file is never read.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from counterweight.elements import Chunk
from counterweight.highlighting import highlight
from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound

from assess_diff.diff import FileDiff
from assess_diff.diff import Line
from assess_diff.diff import LineKind
from assess_diff.ui.rows import TAB_SIZE
from assess_diff.ui.rows import SyntaxLines


@lru_cache(maxsize=2**10)
def language_for(path: str) -> str | None:
    """The Pygments lexer alias for a file, by its name, or `None` when Pygments knows no lexer for it."""
    try:
        lexer = get_lexer_for_filename(path)
    except ClassNotFound:
        return None
    return str(lexer.aliases[0])


def split_lines(chunks: tuple[Chunk, ...]) -> Iterator[tuple[Chunk, ...]]:
    """Highlighted chunks cut into one tuple per line, at each line break inside them."""
    line: list[Chunk] = []
    for chunk in chunks:
        *complete, rest = chunk.content.split("\n")
        for piece in complete:
            if piece:
                line.append(Chunk(content=piece, style=chunk.style))
            yield tuple(line)
            line = []
        if rest:
            line.append(Chunk(content=rest, style=chunk.style))
    yield tuple(line)


def highlighted(lines: tuple[Line, ...], language: str, theme: str) -> SyntaxLines:
    """
    `lines` highlighted together as one document.
    Carriage returns are dropped first, since Pygments reads a lone one as a line break, which would misalign the lines.
    """
    if not lines:
        return {}
    code = "\n".join(line.text.replace("\r", "") for line in lines)
    return dict(zip(lines, split_lines(highlight(code, language, theme, TAB_SIZE)), strict=True))


def syntax_lines(file: FileDiff, theme: str) -> SyntaxLines:
    """Each line of `file` as syntax-colored chunks, or nothing for a file Pygments has no lexer for."""
    language = language_for(file.path)
    if language is None:
        return {}
    new_side = tuple(line for line in file.lines if line.kind is not LineKind.REMOVED)
    colored = highlighted(new_side, language, theme)
    hunks: dict[int | None, list[Line]] = {}
    for line in file.lines:
        if line.kind is LineKind.REMOVED:
            hunks.setdefault(line.hunk, []).append(line)
    for removed in hunks.values():
        colored |= highlighted(tuple(removed), language, theme)
    return colored
