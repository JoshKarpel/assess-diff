from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from counterweight.elements import Chunk
from counterweight.events import KeyPressed
from counterweight.keys import Key
from counterweight.styles.utilities import text_bold
from counterweight.styles.utilities import text_color
from counterweight.styles.utilities import text_dim

from assess_diff.diff import LineKind
from assess_diff.preferences import PreferencesFile
from assess_diff.review import FOLLOW_HEAD
from assess_diff.review import Review
from assess_diff.storage import ReviewStore
from assess_diff.storage import Unreadable
from assess_diff.ui.review_manager import NEW_REVIEW_KEY
from assess_diff.ui.review_manager import entry_chunks
from assess_diff.ui.review_manager import entry_key
from assess_diff.ui.review_manager import unreadable_chunks
from tests.builders import comment
from tests.builders import review
from tests.conftest import Scratch
from tests.harness import CLOCK
from tests.harness import REVIEW_ID
from tests.harness import Harness
from tests.harness import click
from tests.harness import keys
from tests.harness import showing
from tests.harness import typed
from tests.harness import where

ENTER = KeyPressed(key=Key.Enter)
ESCAPE = KeyPressed(key=Key.Escape)


@pytest.fixture
def harness(scratch: Scratch, tmp_path: Path) -> Harness:
    scratch.write("app.py", "one = 1\n")
    scratch.commit()
    scratch.write("app.py", "one = 2\n")
    return Harness(
        scratch=scratch,
        store=ReviewStore(tmp_path / "reviews"),
        preferences=PreferencesFile(tmp_path / "preferences.json"),
    )


def seed(harness: Harness, review_id: str, name: str, *, minutes: int, body: str = "") -> Review:
    """A saved review of the harness's worktree, updated `minutes` after the clock, with one comment if `body`."""
    root = harness.scratch.root
    review = Review.start(review_id, root / ".git", root, "main", FOLLOW_HEAD, CLOCK).model_copy(
        update={"name": name, "updated": CLOCK.replace(minute=minutes)}
    )
    if body:
        review = review.model_copy(
            update={
                "comments": (comment(1, "app.py", 1, 1, ((LineKind.ADDED, "one = 2"),), body=body),),
                "next_number": 2,
            }
        )
    harness.store.save(review, None)
    return review


def dialog(frame: str) -> list[str]:
    """The dialog's entries, from the line after its title to the line before its key hints."""
    lines = frame.splitlines()
    first = next(index for index, line in enumerate(lines) if "│ Reviews" in line) + 1
    last = next(
        index
        for index, line in enumerate(lines)
        if "esc close   ●" in line or "press d again" in line or "Can't read" in line
    )
    return [line.strip(" │") for line in lines[first:last]]


@pytest.mark.parametrize(
    ("is_open", "is_here", "marker"), [(True, True, "● "), (False, True, "  "), (False, False, "↪ ")]
)
def test_entry_marks_the_review_then_names_it_then_its_branch_comments_and_update(
    is_open: bool, is_here: bool, marker: str
) -> None:
    # Already in the local zone, so the entry's conversion to local time leaves it as written wherever this runs.
    updated = datetime(2026, 7, 21, 9, 47).astimezone()
    detached = review(
        comment(1, "a.py", 1, 1, ((LineKind.ADDED, "a"),)),
        comment(2, "b.py", 4, 6, ((LineKind.ADDED, "b"),)),
        branch=None,
    ).model_copy(update={"name": "the detached one", "updated": updated})

    assert entry_chunks(detached, is_open, is_here) == (
        Chunk(content=marker, style=text_color("amber", 300)),
        Chunk(content="the detached one", style=text_bold),
        Chunk(content="  detached HEAD  2 comments  updated 2026-07-21 09:47", style=text_dim),
    )


def test_the_highlight_holds_a_review_by_its_id() -> None:
    assert entry_key(review()) == "0199aaaa-review"


def test_the_highlight_holds_new_review_by_a_key_of_its_own() -> None:
    assert entry_key(None) is NEW_REVIEW_KEY


def test_unreadable_chunks_name_the_file_and_the_first_line_of_why() -> None:
    file = Unreadable(Path("/data/reviews/0199-x.json"), "2 validation errors for Review\nname\n  Field required")

    assert unreadable_chunks(file) == (
        Chunk(content="! ", style=text_color("red", 400)),
        Chunk(content="0199-x.json"),
        Chunk(content="  2 validation errors for Review", style=text_dim),
    )


def garble(harness: Harness, name: str) -> None:
    harness.store.directory.mkdir(parents=True, exist_ok=True)
    (harness.store.directory / name).write_text("not json")


async def test_o_lists_the_files_that_cant_be_read_under_the_reviews(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    garble(harness, "garbled.json")
    (harness.store.directory / "longer-than-the-dialog.json").write_text('{"version": 99}')

    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])

    lines = harness.frames[0].splitlines()
    heading = next(index for index, line in enumerate(lines) if "│ Can't read" in line)
    assert dialog(harness.frames[0]) == [
        "+ New review",
        "● the open one  main  0 comments  updated 2026-06-01 12:05",
        "",
    ]
    assert lines[heading + 1].strip(" │") == "! garbled.json  Expecting value: line 1 column 1 (char 0)"
    assert lines[heading + 2].rstrip().endswith("this assess-diff reads … │")


async def test_reviews_are_managed_beside_a_file_that_cant_be_read(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "doomed", minutes=9)
    garble(harness, "garbled.json")

    await harness.run([*keys("o", "j", "j", "e"), *typed("!"), ENTER, *keys("k", "d", "d"), ESCAPE])

    assert (harness.review().name, harness.store.path("other").exists()) == ("the open one!", False)


async def test_o_lists_new_review_then_this_projects_reviews_most_recent_first(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "another one", minutes=9, body="hi")

    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])

    assert dialog(harness.frames[0]) == [
        "+ New review",
        "another one  main  1 comment  updated 2026-06-01 12:09",
        "● the open one  main  0 comments  updated 2026-06-01 12:05",
    ]


async def test_a_name_too_long_for_the_dialog_is_cut_short_inside_it(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "x" * 120, minutes=5)

    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])

    (entry,) = (line for line in harness.frames[0].splitlines() if "● xxx" in line)
    assert entry.rstrip().endswith("x… │")


async def test_reviews_from_another_worktree_are_marked(harness: Harness, tmp_path: Path) -> None:
    seed(harness, REVIEW_ID, "here", minutes=5)
    elsewhere = seed(harness, "elsewhere", "there", minutes=1)
    harness.store.path("elsewhere").write_text(
        elsewhere.model_copy(update={"worktree": tmp_path / "linked"}).model_dump_json()
    )

    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])

    assert dialog(harness.frames[0])[2].startswith("↪ there")


async def test_enter_on_new_review_starts_one_and_keeps_the_old(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the old one", minutes=5, body="kept")

    await harness.run([*keys("o"), showing("Reviews"), ENTER, harness.shot(), *keys("r"), *typed("fresh"), ESCAPE])

    assert "0 comments" in harness.frames[0]
    assert "Reviews" not in harness.frames[0]
    assert harness.store.load("forked").review.summary == "fresh"
    assert harness.review().comments[0].body == "kept"


async def test_enter_on_a_review_opens_it(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "another one", minutes=9, body="theirs")

    await harness.run([*keys("o", "j", "j", "k"), ENTER, showing("theirs"), harness.shot(), *keys("r"), *typed("s")])

    assert " another one " in harness.frames[0].splitlines()[0]
    assert harness.store.load("other").review.summary == "s"


async def test_e_renames_the_highlighted_review(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "another one", minutes=9)

    await harness.run([*keys("o", "j", "e"), *typed(" renamed"), ENTER, harness.shot(), ESCAPE])

    assert harness.store.load("other").review.name == "another one renamed"
    assert "another one renamed" in dialog(harness.frames[0])[1]


async def test_renaming_the_open_review_shows_in_the_status_line(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "old name", minutes=5)

    await harness.run(
        [
            *keys("o", "j", "e", Key.Backspace, Key.Backspace, Key.Backspace, Key.Backspace),
            *typed("new"),
            ENTER,
            ESCAPE,
            harness.shot(),
        ]
    )

    assert harness.review().name == "old new"
    assert " old new " in harness.frames[0].splitlines()[0]


async def test_a_blank_name_keeps_its_spaces_rather_than_vanishing(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "x", minutes=5)

    await harness.run([*keys("o", "j", "e", Key.Backspace), *typed("  "), ENTER, ESCAPE])

    assert harness.review().name == "  "


async def test_escape_while_renaming_keeps_the_old_name(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "unchanged", minutes=5)

    await harness.run([*keys("o", "j", "e"), *typed("zzz"), ESCAPE, harness.shot(), ESCAPE])

    assert harness.review().name == "unchanged"
    assert "Reviews" in harness.frames[0]


async def test_d_twice_deletes_the_highlighted_review(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "doomed", minutes=9)

    await harness.run([*keys("o", "j", "d"), harness.shot(), *keys("d"), harness.shot(), ESCAPE])

    assert "press d again to delete “doomed”" in harness.frames[0]
    assert not harness.store.path("other").exists()
    assert dialog(harness.frames[1]) == ["+ New review", "● the open one  main  0 comments  updated 2026-06-01 12:05"]


async def test_d_then_another_key_keeps_the_review(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)

    await harness.run([*keys("o", "j", "d", "x", "d"), harness.shot(), ESCAPE])

    assert harness.store.path(REVIEW_ID).exists()
    assert "press d again" in harness.frames[0]


async def test_deleting_the_open_review_starts_a_new_one(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)

    await harness.run([*keys("o", "j", "d", "d"), ESCAPE, *keys("r"), *typed("new"), ESCAPE])

    assert not harness.store.path(REVIEW_ID).exists()
    assert harness.store.load("forked").review.summary == "new"


async def test_deleting_the_open_review_keeps_the_dialog_open_and_answering_keys(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=9)
    seed(harness, "other", "another one", minutes=5)

    await harness.run([*keys("o", "j", "d", "d"), harness.shot(), *keys("j"), ENTER, harness.shot()])

    entries = dialog(harness.frames[0])
    assert (len(entries), entries[0]) == (2, "+ New review")
    assert "another one" in entries[1]
    assert " another one " in harness.frames[1].splitlines()[0]


async def test_new_review_cannot_be_renamed_or_deleted(harness: Harness) -> None:
    await harness.run([*keys("o", "e", "d", "d"), harness.shot(), ESCAPE])

    assert dialog(harness.frames[0]) == ["+ New review"]


async def test_escape_closes_the_dialog(harness: Harness) -> None:
    await harness.run([*keys("o"), showing("Reviews"), ESCAPE, harness.shot()])

    assert "│ Reviews" not in harness.frames[0]


async def test_o_with_the_dialog_open_keeps_it_open(harness: Harness) -> None:
    await harness.run([*keys("o"), showing("Reviews"), *keys("o"), harness.shot(), ESCAPE])

    assert "│ Reviews" in harness.frames[0]


@pytest.mark.parametrize("key", [Key.Tab, Key.BackTab])
async def test_tabbing_out_closes_the_dialog(harness: Harness, key: Key) -> None:
    await harness.run([*keys("o"), showing("Reviews"), *keys(key), harness.shot()])

    assert "│ Reviews" not in harness.frames[0]


async def test_clicking_a_review_opens_it(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "another one", minutes=9)
    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])
    entry = where(harness.frames[0], "another one  main")

    await harness.run([*keys("o"), showing("Reviews"), *click(entry.x, entry.y), harness.shot()])

    assert " another one " in harness.frames[1].splitlines()[0]


async def test_clicking_new_review_starts_one(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5, body="old")
    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])
    entry = where(harness.frames[0], "+ New review")

    await harness.run([*keys("o"), showing("Reviews"), *click(entry.x, entry.y), harness.shot()])

    assert "0 comments" in harness.frames[1]


async def test_keys_the_name_field_leaves_alone_do_nothing_while_renaming(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "first", minutes=5)
    seed(harness, "other", "second", minutes=9)

    await harness.run([*keys("o", "j", "e", Key.Down, "d", "d"), ENTER, ESCAPE])

    assert (harness.store.load("other").review.name, harness.store.path(REVIEW_ID).exists()) == ("seconddd", True)


async def test_the_dialogs_keys_are_pills_as_the_screens_footer_draws_them(harness: Harness) -> None:
    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])

    (footer,) = (
        colored
        for plain, colored in zip(harness.frames[0].splitlines(), harness.colored[0].splitlines(), strict=True)
        if "esc close   ● open" in plain
    )
    assert "48;2;51;65;85" in footer, "the pills are on the dialog's lighter pill color"


async def test_the_highlight_stays_on_a_review_that_renaming_moves_down_the_list(harness: Harness) -> None:
    # Renaming the open review saves it, which dates it at the clock, before the other's update.
    seed(harness, REVIEW_ID, "the open one", minutes=9)
    seed(harness, "other", "another one", minutes=5)

    await harness.run([*keys("o", "j", "e"), *typed(" renamed"), ENTER, *keys("d"), harness.shot(), ESCAPE, ESCAPE])

    assert dialog(harness.frames[0])[1:] == [
        "another one  main  0 comments  updated 2026-06-01 12:05",
        "● the open one renamed  main  0 comments  updated 2026-06-01 12:00",
    ]
    assert "press d again to delete “the open one renamed”" in harness.frames[0]
