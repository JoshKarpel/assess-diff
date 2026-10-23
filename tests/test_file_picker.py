from __future__ import annotations

from pathlib import Path

import pytest
from counterweight.elements import Chunk
from counterweight.events import KeyPressed
from counterweight.keys import Key
from counterweight.styles.utilities import text_color

from assess_diff.navigation import CommentAt
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import SummaryAt
from assess_diff.preferences import PreferencesFile
from assess_diff.storage import ReviewStore
from assess_diff.ui.file_picker import DirectoryRow
from assess_diff.ui.file_picker import FileRow
from assess_diff.ui.file_picker import directory_entry
from assess_diff.ui.file_picker import file_entry
from assess_diff.ui.file_picker import marked
from assess_diff.ui.file_picker import tree_rows
from tests.builders import added
from tests.builders import file_comment
from tests.builders import modified
from tests.builders import removed
from tests.builders import review
from tests.conftest import Scratch
from tests.harness import Harness
from tests.harness import keys
from tests.harness import showing
from tests.harness import typed

CHANGED = modified("src/b.py", removed(4, "four"), added(4, "vier"), added(5, "fünf"))
STATUS = Chunk(content="M ", style=text_color("amber", 300))
ENTER = KeyPressed(key=Key.Enter)
ESCAPE = KeyPressed(key=Key.Escape)


@pytest.mark.parametrize(
    ("cursor", "entry"),
    [
        (SummaryAt(), SummaryAt()),
        (HeaderAt("src/a.py"), HeaderAt("src/a.py")),
        (RowAt("src/b.py", 7), HeaderAt("src/b.py")),
        (CommentAt(2), HeaderAt("src/c.py")),
        (CommentAt(9), None),
    ],
    ids=["summary", "header", "row", "comment", "comment-gone"],
)
def test_marked_is_the_entry_of_the_summary_or_the_file_the_cursor_is_in(
    cursor: Position, entry: HeaderAt | SummaryAt | None
) -> None:
    assert marked(cursor, review(file_comment(1, "src/a.py"), file_comment(2, "src/c.py"))) == entry


def test_file_entry_shows_the_status_and_the_name() -> None:
    assert file_entry(CHANGED, "b.py", 0) == (STATUS, Chunk(content="b.py"), Chunk(content="   "))


def test_file_entry_shows_how_many_comments_a_file_has() -> None:
    assert file_entry(CHANGED, "b.py", 3) == (
        STATUS,
        Chunk(content="b.py"),
        Chunk(content=" ●3", style=text_color("amber", 400)),
    )


@pytest.mark.parametrize(("is_folded", "marker"), [(False, "▾ "), (True, "▸ ")], ids=["open", "folded"])
def test_directory_entry_marks_whether_it_is_folded(is_folded: bool, marker: str) -> None:
    directory = DirectoryRow(path="src/ui", label="ui", depth=1, is_folded=is_folded)

    assert directory_entry(directory) == (
        Chunk(content=marker, style=text_color("slate", 500)),
        Chunk(content="ui/", style=text_color("sky", 300)),
    )


PATHS = ("README.md", "src/app/main.py", "src/app/views.py", "src/lib.py", "tests/unit/test_main.py")


def test_tree_rows_nest_each_directory_a_level_deeper_in_the_order_its_first_file_comes() -> None:
    assert tree_rows(PATHS, frozenset()) == (
        FileRow(index=0, label="README.md", depth=0),
        DirectoryRow(path="src", label="src", depth=0, is_folded=False),
        DirectoryRow(path="src/app", label="app", depth=1, is_folded=False),
        FileRow(index=1, label="main.py", depth=2),
        FileRow(index=2, label="views.py", depth=2),
        FileRow(index=3, label="lib.py", depth=1),
        DirectoryRow(path="tests/unit", label="tests/unit", depth=0, is_folded=False),
        FileRow(index=4, label="test_main.py", depth=1),
    )


def test_tree_rows_hide_what_a_folded_directory_holds() -> None:
    assert tree_rows(PATHS, frozenset({"src", "tests/unit"})) == (
        FileRow(index=0, label="README.md", depth=0),
        DirectoryRow(path="src", label="src", depth=0, is_folded=True),
        DirectoryRow(path="tests/unit", label="tests/unit", depth=0, is_folded=True),
    )


def test_tree_rows_compact_a_chain_of_directories_holding_only_a_directory() -> None:
    assert tree_rows(("a/b/c/one.py", "a/b/c/two.py"), frozenset()) == (
        DirectoryRow(path="a/b/c", label="a/b/c", depth=0, is_folded=False),
        FileRow(index=0, label="one.py", depth=1),
        FileRow(index=1, label="two.py", depth=1),
    )


def picker(frame: str) -> str:
    """The picker's column of a screen: each line up to its right border."""
    return "\n".join(line.split("│", 1)[0] for line in frame.splitlines() if "│" in line)


@pytest.fixture
def harness(scratch: Scratch, tmp_path: Path) -> Harness:
    """A tree of untracked files: `README.md`, `src/app/main.py`, `src/app/views.py` and `src/lib.py`."""
    scratch.write("seed", "")
    scratch.commit()
    for path in ("README.md", "src/app/main.py", "src/app/views.py", "src/lib.py"):
        scratch.write(path, f"# {path}\n")
    return Harness(
        scratch=scratch,
        store=ReviewStore(tmp_path / "reviews"),
        preferences=PreferencesFile(tmp_path / "preferences.json"),
    )


async def test_enter_on_a_directory_folds_it_and_unfolds_it(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Down, Key.Down), ENTER, harness.shot(), ENTER, harness.shot()])

    assert [("▸ src/" in picker(frame), "views.py" in picker(frame)) for frame in harness.frames] == [
        (True, False),
        (False, True),
    ]


async def test_left_folds_a_directory_and_right_unfolds_it(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Down, Key.Down, Key.Left), harness.shot(), *keys(Key.Right), harness.shot()])

    assert ["views.py" in picker(frame) for frame in harness.frames] == [False, True]


async def test_enter_on_a_file_in_the_tree_comments_there(harness: Harness) -> None:
    await harness.run([*keys("f", "j", "j", "j", "j"), ENTER, *keys(Key.Down), ENTER, *typed("main"), ESCAPE])

    assert [(c.anchor.path, c.body) for c in harness.review().comments] == [("src/app/main.py", "main")]


async def test_slash_filters_the_files_and_enter_jumps_to_the_match(harness: Harness) -> None:
    await harness.run(
        [*keys("f", "/"), *typed("vw"), harness.shot(), ENTER, *keys(Key.Down), ENTER, *typed("v"), ESCAPE]
    )

    assert picker(harness.frames[0]).split() == ["/", "vw", "?", "src/app/views.py"]
    assert [c.anchor.path for c in harness.review().comments] == ["src/app/views.py"]


async def test_escape_from_the_filter_goes_back_to_the_tree(harness: Harness) -> None:
    await harness.run([*keys("f", "/"), *typed("zz"), harness.shot(), ESCAPE, harness.shot()])

    assert ["No file matches" in frame for frame in harness.frames] == [True, False]
    assert "▾ src/" in picker(harness.frames[1])


async def test_the_filter_closes_when_focus_leaves_it(harness: Harness) -> None:
    await harness.run([*keys("f", "/"), *typed("lib"), KeyPressed(key=Key.Tab), showing("▾ src/"), harness.shot()])

    assert "/ lib" not in picker(harness.frames[0])


async def test_down_and_up_move_through_the_filters_matches(harness: Harness) -> None:
    await harness.run(
        [
            *keys("f", "/"),
            showing("/ "),
            *typed("src"),
            *keys(Key.Down, Key.Down, Key.Up),
            ENTER,
            *keys(Key.Down),
            ENTER,
            *typed("views"),
            ESCAPE,
        ]
    )

    assert [c.anchor.path for c in harness.review().comments] == ["src/app/views.py"]


async def test_enter_on_the_summary_entry_jumps_to_the_summary(harness: Harness) -> None:
    await harness.run([*keys(Key.Tab, "f"), ENTER, ENTER, *typed("from the picker"), ESCAPE])

    assert harness.review().summary == "from the picker"
    assert harness.review().comments == ()


async def test_the_summary_entry_tops_the_picker(harness: Harness) -> None:
    await harness.run([harness.shot()])

    assert picker(harness.frames[0]).split()[:2] == ["≡", "Summary"]
