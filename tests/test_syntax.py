from __future__ import annotations

from counterweight.elements import Chunk
from counterweight.styles import CellStyle

from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.ui.syntax import highlighted
from assess_diff.ui.syntax import language_for
from assess_diff.ui.syntax import split_lines
from assess_diff.ui.syntax import syntax_lines
from tests.builders import added
from tests.builders import context
from tests.builders import modified
from tests.builders import removed

STYLE = CellStyle(bold=True)


def text(chunks: tuple[Chunk, ...]) -> str:
    return "".join(chunk.content for chunk in chunks)


def test_language_for_a_known_and_an_unknown_file_name() -> None:
    assert (language_for("app.py"), language_for("notes.unknown-extension")) == ("python", None)


def test_split_lines_cuts_at_line_breaks_inside_chunks() -> None:
    chunks = (Chunk(content="ab\ncd", style=STYLE), Chunk(content="e\n\nf"))

    assert list(split_lines(chunks)) == [
        (Chunk(content="ab", style=STYLE),),
        (Chunk(content="cd", style=STYLE), Chunk(content="e")),
        (),
        (Chunk(content="f"),),
    ]


def test_highlighted_gives_each_line_its_own_text() -> None:
    lines = (context(1, 1, "x = 1"), added(2, "\ty = 'two'\r"))

    colored = highlighted(lines, "python", "github-dark")

    assert [text(colored[line]) for line in lines] == ["x = 1", "    y = 'two'"]


def test_highlighted_of_no_lines_is_nothing() -> None:
    assert highlighted((), "python", "github-dark") == {}


def test_highlighted_colors_tokens() -> None:
    (line,) = lines = (added(1, "def f(): pass"),)

    styles = {chunk.content: chunk.style for chunk in highlighted(lines, "python", "monokai")[line]}

    assert styles["def"] != styles["f"]


def test_syntax_lines_cover_the_working_tree_and_each_hunks_removed_lines() -> None:
    file = modified(
        "app.py",
        context(1, 1, "a = 1"),
        removed(2, "b = 2"),
        added(2, "b = 3"),
        removed(3, "c = 4", hunk=1),
    )

    colored = syntax_lines(file, "github-dark")

    assert {line: text(chunks) for line, chunks in colored.items()} == {line: line.text for line in file.lines}


def test_syntax_lines_highlight_each_hunks_removed_lines_apart() -> None:
    opening, later = removed(2, 'x = """', hunk=0), removed(9, "y = 1", hunk=1)

    colored = syntax_lines(modified("app.py", opening, later), "monokai")

    assert colored == highlighted((opening,), "python", "monokai") | highlighted((later,), "python", "monokai")


def test_syntax_lines_of_an_unknown_language_are_none() -> None:
    file = FileDiff(path="data.zzz", old_path=None, status=FileStatus.MODIFIED, lines=(added(1, "x"),), hunk_count=1)

    assert syntax_lines(file, "github-dark") == {}
