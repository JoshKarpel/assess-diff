from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from counterweight.asciicast import Recording
from counterweight.controls import FocusOn
from counterweight.controls import Marker
from counterweight.elements import Chunk
from counterweight.events import InputEvent
from counterweight.events import KeyPressed
from counterweight.events import MouseScrolledDown
from counterweight.events import MouseScrolledLeft
from counterweight.events import MouseScrolledRight
from counterweight.events import MouseScrolledUp
from counterweight.events import Receiver
from counterweight.focus import FocusHandle
from counterweight.focus import Subtree
from counterweight.geometry import Position
from counterweight.geometry import Rects
from counterweight.geometry import Region
from counterweight.keys import Key
from counterweight.styles.utilities import border
from counterweight.styles.utilities import border_color
from counterweight.styles.utilities import border_lightrounded
from counterweight.styles.utilities import col
from counterweight.styles.utilities import margin_left
from counterweight.styles.utilities import text_bold
from counterweight.styles.utilities import text_color
from counterweight.widgets import text_area

from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.diff import LineKind
from assess_diff.navigation import FileLayout
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position as CursorPosition
from assess_diff.navigation import RowAt
from assess_diff.navigation import SummaryAt
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.preferences import PreferencesFile
from assess_diff.storage import ReviewStore
from assess_diff.ui.diff_view import COMMENT_STYLES
from assess_diff.ui.diff_view import ROWS_PER_BLOCK
from assess_diff.ui.diff_view import Actions
from assess_diff.ui.diff_view import Run
from assess_diff.ui.diff_view import border_title
from assess_diff.ui.diff_view import comment_title
from assess_diff.ui.diff_view import editor
from assess_diff.ui.diff_view import header_chunks
from assess_diff.ui.diff_view import is_near
from assess_diff.ui.diff_view import near_cursor
from assess_diff.ui.diff_view import runs
from assess_diff.ui.diff_view import sideways
from assess_diff.ui.rows import Highlight
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import diff_of
from tests.builders import modified
from tests.builders import removed
from tests.conftest import Scratch
from tests.harness import Harness
from tests.harness import keys
from tests.harness import showing
from tests.harness import typed

NONE, CURSOR, SELECTED = Highlight.NONE, Highlight.CURSOR, Highlight.SELECTED
BLOCK = ROWS_PER_BLOCK

FINISHED = FocusOn(FocusHandle())
ACTIONS = Actions(
    press_row=lambda path, index, extend: None,
    press_position=lambda position: None,
    edit_comment=lambda number, edit: "",
    edit_summary=lambda edit: "",
    finish_editing=lambda: FINISHED,
)
BOX = Region(left=4, top=9, right=30, bottom=14)
RECEIVER = Receiver(
    rects=Rects(content=BOX, padding=BOX, border=BOX, margin=BOX, clip=BOX, visible=BOX, screen=BOX),
    is_target=False,
    focus_handle=None,
    is_focused=False,
    subtree=Subtree(),
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


def edit_nothing(edit: Callable[[str], str]) -> str:  # type: ignore[empty-body]  # never called
    """An editor's `edit`, which these tests compare by identity rather than call."""


def test_editor_is_a_multiline_text_area_in_the_comment_styles_with_its_title_on_the_border() -> None:
    title = (Chunk(content="#6"),)
    handle = FocusHandle()

    drawn = editor(title, "a body", edit_nothing, handle, ACTIONS, margin_left(3))

    assert (drawn.style, drawn.children) == (
        col | border | border_lightrounded | border_color("amber", 300) | margin_left(3),
        (
            border_title(title),
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
        (False, (Chunk(content=" #14 ", style=text_bold | text_color("amber", 300)),)),
        (
            True,
            (
                Chunk(content=" #14 ", style=text_bold | text_color("amber", 300)),
                Chunk(content="outdated ", style=SLATE),
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


WIDE = "value = compute(" + ", ".join(f"argument_{index}" for index in range(14)) + ")  # END"
IN_THE_DIFF = Position(60, 10)


@pytest.fixture
def wide(scratch: Scratch, tmp_path: Path) -> Harness:
    """`wide.py` with its short first line and its line wider than the screen both changed."""
    scratch.write("wide.py", f"short = 1\n{WIDE.replace('value', 'old')}\n")
    scratch.commit()
    scratch.write("wide.py", f"short = 2\n{WIDE}\n")
    return Harness(
        scratch=scratch,
        store=ReviewStore(tmp_path / "reviews"),
        preferences=PreferencesFile(tmp_path / "preferences.json"),
    )


@pytest.mark.parametrize("key", [Key.Right, "l"])
async def test_right_scrolls_the_code_sideways_and_keeps_the_line_numbers(wide: Harness, key: str) -> None:
    await wide.run([*keys(key), wide.shot()])

    assert "      2 + …ompute(argument_0" in wide.frames[0]


@pytest.mark.parametrize("key", [Key.Left, "h"])
async def test_left_scrolls_back(wide: Harness, key: str) -> None:
    await wide.run([*keys("l", "l", key), wide.shot()])

    assert "      2 + …ompute(argument_0" in wide.frames[0]


async def test_zero_scrolls_back_to_the_start_of_the_lines(wide: Harness) -> None:
    await wide.run([*keys("l", "l", "0"), wide.shot()])

    assert "      2 + value = compute(" in wide.frames[0]


@pytest.mark.parametrize("before", [(), ("s",)], ids=["unified", "split"])
async def test_scrolling_stops_where_the_widest_line_ends(wide: Harness, before: tuple[str, ...]) -> None:
    await wide.run([*keys(*before, *["l"] * 40), wide.shot(), *keys("h"), wide.shot()])

    assert [any(line.rstrip().endswith("# END") for line in frame.splitlines()) for frame in wide.frames] == [
        True,
        False,
    ], "the end of the widest line reaches the right edge, and one step back scrolls it out of view again"


@pytest.mark.parametrize(
    "event",
    [MouseScrolledRight(absolute=IN_THE_DIFF), MouseScrolledDown(absolute=IN_THE_DIFF, is_shift_held=True)],
    ids=["sideways", "shift"],
)
async def test_the_wheel_scrolls_right(wide: Harness, event: MouseScrolledRight | MouseScrolledDown) -> None:
    await wide.run([event, wide.shot()])

    assert "      2 + … = compute(" in wide.frames[0]


@pytest.mark.parametrize(
    "event",
    [MouseScrolledLeft(absolute=IN_THE_DIFF), MouseScrolledUp(absolute=IN_THE_DIFF, is_shift_held=True)],
    ids=["sideways", "shift"],
)
async def test_the_wheel_scrolls_left(wide: Harness, event: MouseScrolledLeft | MouseScrolledUp) -> None:
    await wide.run([*keys("l"), event, wide.shot()])

    assert "      2 + … = compute(" in wide.frames[0]


async def test_the_wheel_without_shift_still_scrolls_down(wide: Harness) -> None:
    await wide.run([MouseScrolledDown(absolute=IN_THE_DIFF), wide.shot()])

    assert "      2 + value = compute(" in wide.frames[0]


async def test_the_scroll_keys_type_into_a_comment(wide: Harness) -> None:
    await wide.run([*keys(Key.Down, Key.Down, Key.Enter), *typed("hl0"), *keys(Key.Escape), wide.shot()])

    assert [c.body for c in wide.review().comments] == ["hl0"]
    assert "      2 + value = compute(" in wide.frames[0]


RIGHT = KeyPressed(key=Key.Right)


@pytest.mark.parametrize(
    ("event", "offset", "limit", "expected"),
    [
        (RIGHT, 5, 100, 13),
        (KeyPressed(key="l"), 5, 100, 13),
        (KeyPressed(key=Key.Left), 21, 100, 13),
        (KeyPressed(key="h"), 21, 100, 13),
        (KeyPressed(key="0"), 21, 100, 0),
        (MouseScrolledRight(absolute=IN_THE_DIFF), 5, 100, 9),
        (MouseScrolledDown(absolute=IN_THE_DIFF, is_shift_held=True), 5, 100, 9),
        (MouseScrolledLeft(absolute=IN_THE_DIFF), 21, 100, 17),
        (MouseScrolledUp(absolute=IN_THE_DIFF, is_shift_held=True), 21, 100, 17),
        (RIGHT, 95, 99, 99),
        (KeyPressed(key=Key.Left), 3, 99, 0),
        (KeyPressed(key=Key.Left), 50, 30, 22),
    ],
    ids=[
        "right",
        "l",
        "left",
        "h",
        "zero",
        "wheel-right",
        "shift-wheel-down",
        "wheel-left",
        "shift-wheel-up",
        "stops-at-the-limit",
        "stops-at-the-start",
        "back-from-past-the-limit",
    ],
)
def test_sideways_moves_the_offset_it_is_given(event: InputEvent, offset: int, limit: int, expected: int) -> None:
    scroll = sideways(event, limit)

    assert scroll is not None
    assert scroll(offset) == expected


def test_sideways_steps_add_up_when_several_come_between_renders() -> None:
    scroll = sideways(RIGHT, 100)

    assert scroll is not None
    assert scroll(scroll(scroll(0))) == 24


@pytest.mark.parametrize(
    "event", [KeyPressed(key="x"), KeyPressed(key=Key.Down), MouseScrolledDown(absolute=IN_THE_DIFF)]
)
def test_sideways_leaves_other_events_alone(event: InputEvent) -> None:
    assert sideways(event, 100) is None


VIEW = Region(left=0, top=10, right=80, bottom=30)


@pytest.mark.parametrize(
    ("extent", "expected"),
    [
        (Region(left=0, top=15, right=80, bottom=25), True),
        (Region(left=0, top=0, right=80, bottom=100), True),
        (Region(left=0, top=-9, right=80, bottom=-5), True),
        (Region(left=0, top=45, right=80, bottom=60), True),
        (Region(left=0, top=-30, right=80, bottom=-10), False),
        (Region(left=0, top=50, right=80, bottom=70), False),
    ],
    ids=["inside", "around", "within-a-view-above", "within-a-view-below", "a-view-above", "a-view-below"],
)
def test_is_near_takes_in_a_views_height_either_side(extent: Region, expected: bool) -> None:
    assert is_near(extent, VIEW) is expected


@pytest.fixture
def many(scratch: Scratch, tmp_path: Path) -> Harness:
    """Six files, each 40 changed lines with a wide one at its end, so the last file is screens below the first."""
    paths = [f"file_{index}.py" for index in range(6)]
    for path in paths:
        scratch.write(path, "".join(f"line_{n} = {n}\n" for n in range(40)))
    scratch.commit()
    for index, path in enumerate(paths):
        scratch.write(
            path,
            "".join(f"line_{n} = {n + 1}\n" for n in range(39)) + f"{WIDE.replace('compute', f'compute_{index}')}\n",
        )
    return Harness(
        scratch=scratch,
        store=ReviewStore(tmp_path / "reviews"),
        preferences=PreferencesFile(tmp_path / "preferences.json"),
    )


async def test_a_file_far_from_the_view_catches_up_with_the_scroll_once_it_comes_near(many: Harness) -> None:
    await many.run([*keys("l", Key.End), showing("…ompute_5("), many.shot()])

    assert "40 + …ompute_5(argument_0" in many.frames[0]


FIVE_ROWS = tuple(context(n, n, f"line {n}") for n in range(1, 6))
THREE_FILES = build_document(
    diff_of(modified("a.py", *FIVE_ROWS), modified("b.py", *FIVE_ROWS), modified("c.py", *FIVE_ROWS)),
    (),
    ViewMode.UNIFIED,
)
"""The summary at 0, then each file's header and its five rows: `a.py` at 1 to 6, `b.py` at 7 to 12, `c.py` at 13 to 18."""


@pytest.mark.parametrize(
    ("cursor", "reach", "near"),
    [
        (RowAt("b.py", 3), 0, {"b.py"}),
        (RowAt("b.py", 3), 4, {"b.py", "c.py"}),
        (RowAt("b.py", 3), 5, {"a.py", "b.py", "c.py"}),
        (HeaderAt("b.py"), 1, {"a.py", "b.py"}),
        (RowAt("b.py", 4), 1, {"b.py", "c.py"}),
        (SummaryAt(), 0, set()),
        (SummaryAt(), 1, {"a.py"}),
    ],
    ids=[
        "own-file",
        "short-of-the-file-before",
        "reaching-the-file-before",
        "header",
        "last-row",
        "summary",
        "past-summary",
    ],
)
def test_near_cursor_is_the_files_within_reach_of_its_position(
    cursor: CursorPosition, reach: int, near: set[str]
) -> None:
    assert near_cursor(THREE_FILES, cursor, reach) == near


def frames_after_marker(cast: Path) -> int:
    """How many frames a recording holds after its marker."""
    events = [json.loads(line) for line in cast.read_text().splitlines()[1:]]
    marker = next(index for index, event in enumerate(events) if event[1] == "m")
    return sum(event[1] == "o" for event in events[marker + 1 :])


@pytest.mark.parametrize(
    ("before", "jump", "landed"),
    [((), Key.End, "…ompute_5("), ((Key.End,), Key.Home, "Review summary")],
    ids=["end", "home"],
)
async def test_a_jump_while_scrolled_sideways_draws_where_it_lands_scrolled_in_one_frame(
    many: Harness, tmp_path: Path, before: tuple[Key, ...], jump: Key, landed: str
) -> None:
    cast = tmp_path / "jump.cast"

    await many.run(
        [*keys(*before, "l"), showing("…"), Marker("jump"), *keys(jump), showing(landed)],
        recording=Recording(cast),
    )

    assert frames_after_marker(cast) == 1, "a frame of the files at the old offset, then one catching up, is two"
