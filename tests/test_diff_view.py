from __future__ import annotations

import pytest
from counterweight.controls import FocusOn
from counterweight.elements import Chunk
from counterweight.elements import Text
from counterweight.events import KeyPressed
from counterweight.events import Receiver
from counterweight.focus import FocusHandle
from counterweight.focus import Subtree
from counterweight.geometry import Rects
from counterweight.geometry import Region
from counterweight.hooks.impls import NodeKey
from counterweight.keys import Key
from counterweight.styles.utilities import col
from counterweight.styles.utilities import margin_left
from counterweight.styles.utilities import text_bold
from counterweight.styles.utilities import text_color
from counterweight.styles.utilities import text_wrap_none
from counterweight.widgets import text_area

from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.diff import LineKind
from assess_diff.navigation import FileLayout
from assess_diff.ui.diff_view import COMMENT_STYLES
from assess_diff.ui.diff_view import ROWS_PER_BLOCK
from assess_diff.ui.diff_view import Actions
from assess_diff.ui.diff_view import Run
from assess_diff.ui.diff_view import comment_title
from assess_diff.ui.diff_view import editor
from assess_diff.ui.diff_view import header_chunks
from assess_diff.ui.diff_view import runs
from assess_diff.ui.rows import Highlight
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import removed

NONE, CURSOR, SELECTED = Highlight.NONE, Highlight.CURSOR, Highlight.SELECTED
BLOCK = ROWS_PER_BLOCK

FINISHED = FocusOn(FocusHandle())
ACTIONS = Actions(
    press_row=lambda path, index, extend: None,
    press_position=lambda position: None,
    edit_comment=lambda number, edit: None,
    edit_summary=lambda edit: None,
    finish_editing=lambda: FINISHED,
)
BOX = Region(left=4, top=9, right=30, bottom=14)
RECEIVER = Receiver(
    rects=Rects(content=BOX, padding=BOX, border=BOX, margin=BOX, clip=BOX, visible=BOX),
    is_target=False,
    focus_handle=None,
    is_focused=False,
    subtree=Subtree(key=NodeKey(3)),
    scroll=None,
)
SLATE = text_color("slate", 400)


def test_runs_of_a_file_shorter_than_a_block_with_nothing_on_it_is_one_run() -> None:
    assert runs(BLOCK - 3, frozenset(), None, None) == (Run(0, BLOCK - 3, NONE),)


def test_runs_cut_at_the_end_of_each_block() -> None:
    assert runs(2 * BLOCK + 5, frozenset(), None, None) == (
        Run(0, BLOCK, NONE),
        Run(BLOCK, 2 * BLOCK, NONE),
        Run(2 * BLOCK, 2 * BLOCK + 5, NONE),
    )


def test_runs_cut_around_the_cursor_within_its_block_only() -> None:
    assert runs(2 * BLOCK + 5, frozenset(), BLOCK + 7, None) == (
        Run(0, BLOCK, NONE),
        Run(BLOCK, BLOCK + 7, NONE),
        Run(BLOCK + 7, BLOCK + 8, CURSOR),
        Run(BLOCK + 8, 2 * BLOCK, NONE),
        Run(2 * BLOCK, 2 * BLOCK + 5, NONE),
    )


def test_runs_cut_a_selection_across_a_block_end() -> None:
    assert runs(BLOCK + 9, frozenset(), BLOCK + 2, (BLOCK - 4, BLOCK + 2)) == (
        Run(0, BLOCK - 4, NONE),
        Run(BLOCK - 4, BLOCK, SELECTED),
        Run(BLOCK, BLOCK + 2, SELECTED),
        Run(BLOCK + 2, BLOCK + 3, CURSOR),
        Run(BLOCK + 3, BLOCK + 9, NONE),
    )


def test_runs_select_through_the_last_row_of_a_selection_made_upward() -> None:
    assert runs(12, frozenset(), 3, (3, 8)) == (
        Run(0, 3, NONE),
        Run(3, 4, CURSOR),
        Run(4, 9, SELECTED),
        Run(9, 12, NONE),
    )


def test_runs_cut_after_a_row_with_comments_after_it() -> None:
    assert runs(11, frozenset({3}), None, None) == (Run(0, 4, NONE), Run(4, 11, NONE))


def edit_nothing(edit: object) -> None:
    """An editor's `edit`, which these tests compare by identity rather than call."""


def test_editor_is_its_title_over_a_multiline_text_area_in_the_comment_styles() -> None:
    title = (Chunk(content="#6"),)
    handle = FocusHandle()

    drawn = editor(title, "a body", edit_nothing, handle, ACTIONS, margin_left(3))

    assert (drawn.style, drawn.children) == (
        col | margin_left(3),
        (
            Text(content=title, style=text_wrap_none),
            text_area("a body", edit_nothing, handle=handle, enter_inserts_newline=True, styles=COMMENT_STYLES),
        ),
    )


@pytest.mark.parametrize(("key", "response"), [(Key.Escape, FINISHED), ("q", None)])
def test_editor_finishes_editing_on_escape_only(key: str, response: FocusOn | None) -> None:
    drawn = editor((), "", edit_nothing, FocusHandle(), ACTIONS, margin_left(3))

    assert drawn.on_input is not None
    assert drawn.on_input(KeyPressed(key=key), RECEIVER) == response


@pytest.mark.parametrize(
    ("is_outdated", "expected"),
    [
        (False, (Chunk(content="#14", style=text_bold | text_color("amber", 300)),)),
        (
            True,
            (
                Chunk(content="#14", style=text_bold | text_color("amber", 300)),
                Chunk(content="  outdated", style=SLATE),
            ),
        ),
    ],
)
def test_comment_title_is_its_number_then_whether_its_outdated(is_outdated: bool, expected: tuple[Chunk, ...]) -> None:
    assert comment_title(comment(14, "app.py", 2, 3, ((LineKind.ADDED, "x"),)), is_outdated) == expected


def layout_of(file: FileDiff) -> FileLayout:
    return FileLayout(file=file, rows=(), placed=(), hunk_starts=frozenset())


LINES = (context(1, 1, "a"), removed(2, "b"), added(2, "c"), added(3, "d"), added(4, "e"))


@pytest.mark.parametrize(
    ("file", "name", "status"),
    [
        (FileDiff("src/kept.py", None, FileStatus.MODIFIED, LINES, 1), " src/kept.py", ""),
        (FileDiff("src/new.py", "lib/old.py", FileStatus.RENAMED, LINES, 1), " lib/old.py → src/new.py", " (renamed)"),
    ],
)
def test_header_names_the_file_any_rename_and_status_then_counts_its_lines(
    file: FileDiff, name: str, status: str
) -> None:
    assert header_chunks(layout_of(file)) == (
        Chunk(content=name, style=text_bold),
        Chunk(content=status, style=SLATE),
        Chunk(content="  +3", style=text_color("green", 400)),
        Chunk(content=" -1", style=text_color("red", 400)),
    )
