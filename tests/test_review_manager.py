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

from assess_diff.diff import BaseMode
from assess_diff.diff import LineKind
from assess_diff.preferences import PreferencesFile
from assess_diff.review import Review
from assess_diff.storage import ReviewStore
from assess_diff.ui.review_manager import entry_chunks
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
    review = Review.start(review_id, root / ".git", root, "main", BaseMode.HEAD, CLOCK).model_copy(
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
    last = next(index for index, line in enumerate(lines) if "esc close   ●" in line or "press d again" in line)
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


async def test_o_lists_new_review_then_this_projects_reviews_most_recent_first(harness: Harness) -> None:
    seed(harness, REVIEW_ID, "the open one", minutes=5)
    seed(harness, "other", "another one", minutes=9, body="hi")

    await harness.run([*keys("o"), showing("Reviews"), harness.shot()])

    assert dialog(harness.frames[0]) == [
        "+ New review",
        "another one  main  1 comment  updated 2026-06-01 12:09",
        "● the open one  main  0 comments  updated 2026-06-01 12:05",
    ]


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
