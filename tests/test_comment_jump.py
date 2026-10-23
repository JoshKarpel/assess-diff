from __future__ import annotations

import pytest
from counterweight.elements import Chunk
from counterweight.styles.utilities import default
from counterweight.styles.utilities import text_bold
from counterweight.styles.utilities import text_color
from counterweight.styles.utilities import text_dim

from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.review import Comment
from assess_diff.ui.comment_jump import place
from assess_diff.ui.comment_jump import preview
from assess_diff.ui.comment_jump import wanted
from tests.builders import comment
from tests.builders import file_comment
from tests.builders import review

THIRD = comment(3, "src/app.py", 42, 45, ((LineKind.ADDED, "x"),), body="first line\nsecond line\nthird line")
SEVENTH = file_comment(7, "src/views.py")


@pytest.mark.parametrize(("number", "found"), [("", None), ("3", THIRD), ("7", SEVENTH), ("5", None), ("03", THIRD)])
def test_wanted_is_the_comment_the_typed_number_names(number: str, found: Comment | None) -> None:
    assert wanted(number, review(THIRD, SEVENTH)) == found


@pytest.mark.parametrize(
    ("commented", "expected"),
    [
        (comment(1, "a.py", 9, 9, ()), "a.py:9"),
        (comment(2, "b.py", 4, 6, ()), "b.py:4-6"),
        (comment(3, "c.py", 8, 8, (), side=Side.OLD), "c.py, removed line 8"),
        (comment(4, "d.py", 10, 12, (), side=Side.OLD), "d.py, removed lines 10-12"),
        (file_comment(5, "e.py"), "e.py"),
    ],
    ids=["new-line", "new-range", "old-line", "old-range", "file"],
)
def test_place_says_where_a_comment_points(commented: Comment, expected: str) -> None:
    assert place(commented.anchor) == expected


def test_preview_shows_a_found_comments_place_and_first_line() -> None:
    assert preview("3", THIRD) == (
        Chunk(content="#3", style=text_bold | text_color("amber", 300)),
        Chunk(content=" src/app.py:42-45", style=text_color("slate", 300)),
        Chunk.newline(),
        Chunk(content="first line", style=default),
    )


def test_preview_marks_an_empty_comment() -> None:
    *_, body = preview("7", SEVENTH)

    assert body == Chunk(content="(empty)", style=text_dim)


@pytest.mark.parametrize(("number", "hint"), [("", "Type a comment's number"), ("12", "No comment #12")])
def test_preview_says_why_there_is_no_comment(number: str, hint: str) -> None:
    assert preview(number, None) == (Chunk(content=hint, style=text_color("slate", 500)),)
