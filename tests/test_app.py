from __future__ import annotations

import gc
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from counterweight.autopilot import WaitFor
from counterweight.controls import CopyToClipboard
from counterweight.elements import Chunk
from counterweight.events import KeyPressed
from counterweight.focus import FocusHandle
from counterweight.headless import Highlight
from counterweight.keys import Key
from counterweight.styles.utilities import text_bg
from counterweight.styles.utilities import text_bold
from counterweight.styles.utilities import text_color

from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.export import to_markdown
from assess_diff.git import compute_diff
from assess_diff.git import find_repository
from assess_diff.git import resolve_base
from assess_diff.navigation import Scope
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.preferences import Preferences
from assess_diff.preferences import PreferencesFile
from assess_diff.review import FOLLOW_HEAD
from assess_diff.review import FOLLOW_MERGE_BASE
from assess_diff.review import Comment
from assess_diff.review import ExcerptLine
from assess_diff.review import FileAnchor
from assess_diff.review import NewAnchor
from assess_diff.review import OldAnchor
from assess_diff.review import Review
from assess_diff.review import edit_summary
from assess_diff.review import is_outdated
from assess_diff.review import pinned
from assess_diff.storage import Listing
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewStore
from assess_diff.storage import Unreadable
from assess_diff.ui.app import INITIAL_SESSION
from assess_diff.ui.app import Jumping
from assess_diff.ui.app import Managing
from assess_diff.ui.app import Naming
from assess_diff.ui.app import PickingTheme
from assess_diff.ui.app import Session
from assess_diff.ui.app import copied
from assess_diff.ui.app import key_hints
from assess_diff.ui.app import status_line
from assess_diff.ui.app import unreadable_notice
from assess_diff.ui.pills import ON_SCREEN
from assess_diff.ui.pills import key_pills
from assess_diff.ui.pills import pills
from assess_diff.ui.theme_picker import Query
from tests.builders import added
from tests.builders import comment
from tests.builders import diff_of
from tests.builders import modified
from tests.builders import removed
from tests.builders import review
from tests.conftest import Scratch
from tests.harness import HEIGHT
from tests.harness import REVIEW_ID
from tests.harness import Harness
from tests.harness import click
from tests.harness import keys
from tests.harness import screen_text
from tests.harness import showing
from tests.harness import typed
from tests.harness import where

ESCAPE = KeyPressed(key=Key.Escape)
ENTER = KeyPressed(key=Key.Enter)
TAB = KeyPressed(key=Key.Tab)


def functions(changed: dict[int, int] | None = None) -> str:
    changed = changed or {}
    return "".join(f"def f{n}():\n    return {changed.get(n, n)}\n\n" for n in range(30))


@pytest.fixture
def harness(scratch: Scratch, tmp_path: Path) -> Harness:
    """`app.py` with `return 3` changed to `return 6` (line 11) and `return 13` to `return 26` (line 41), and an untracked `notes.md`."""
    scratch.write("app.py", functions())
    scratch.commit()
    scratch.write("app.py", functions({3: 6, 13: 26}))
    scratch.write("notes.md", "# Notes\n\nfirst\n")
    return Harness(
        scratch=scratch,
        store=ReviewStore(tmp_path / "reviews"),
        preferences=PreferencesFile(tmp_path / "preferences.json"),
    )


@pytest.fixture
def whole_harness(harness: Harness) -> Harness:
    """`harness` showing whole files, for tests that need more rows than the hunks and their context take."""
    harness.preferences.save(Preferences.defaults().model_copy(update={"scope": Scope.WHOLE}))
    return harness


def lines(comment: Comment) -> NewAnchor | OldAnchor:
    """The anchor of a comment on lines, as every comment made with Enter on a row is."""
    assert isinstance(comment.anchor, NewAnchor | OldAnchor)
    return comment.anchor


async def test_a_diff_computed_already_is_drawn_in_the_first_frame(harness: Harness) -> None:
    repository = await find_repository(harness.scratch.root)
    computed = await compute_diff(repository, await resolve_base(repository, FOLLOW_HEAD, "HEAD"))

    await harness.run([harness.shot()], loaded="", initial_diff=computed)

    assert "return 6" in harness.frames[0]
    assert "reading the diff" not in harness.frames[0]


async def test_the_drawn_diff_is_frozen_out_of_the_garbage_collectors_passes(harness: Harness) -> None:
    await harness.run([harness.shot()])

    assert gc.get_freeze_count() > 0


async def test_tab_reaches_the_first_hunk_and_enter_comments_on_it(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("why six?"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.number, lines(made).side, lines(made).start, lines(made).end, made.body) == (
        1,
        Side.OLD,
        11,
        11,
        "why six?",
    )
    assert made.excerpt == (ExcerptLine(kind=LineKind.REMOVED, text="    return 3"),)


async def test_extending_the_selection_comments_on_the_range(harness: Harness) -> None:
    await harness.run([TAB, *keys(Key.Down, "J", "J"), ENTER, *typed("range"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).path, lines(made).side, lines(made).start, lines(made).end) == ("app.py", Side.NEW, 11, 13)
    assert [line.kind for line in made.excerpt] == [LineKind.ADDED, LineKind.CONTEXT, LineKind.CONTEXT]


async def test_shift_arrows_extend_and_shrink_the_selection(harness: Harness) -> None:
    await harness.run([TAB, *keys(Key.Down, Key.ShiftDown, Key.ShiftDown, Key.ShiftUp), ENTER, ESCAPE])

    assert harness.review().comments == ()
    assert harness.review().next_number == 2


async def test_escape_clears_the_selection(harness: Harness) -> None:
    await harness.run([TAB, *keys("J", Key.Escape), ENTER, *typed("one line"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).side, lines(made).start, lines(made).end) == (Side.NEW, 11, 11)


async def test_escape_on_an_empty_new_comment_discards_it(harness: Harness) -> None:
    await harness.run([TAB, ENTER, ESCAPE, harness.shot()])

    assert (harness.review().comments, harness.review().next_number) == ((), 2)


async def test_n_and_shift_n_jump_between_comments(harness: Harness) -> None:
    await harness.run(
        [
            *(TAB, ENTER, *typed("one"), ESCAPE),
            *(TAB, ENTER, *typed("two"), ESCAPE),
            *keys(Key.Home, "n", "n", "N"),
            *(ENTER, *typed("!"), ESCAPE),
        ]
    )

    assert [(c.number, lines(c).start, c.body) for c in harness.review().comments] == [(1, 11, "one!"), (2, 41, "two")]


TWO_COMMENTS = [*(TAB, ENTER, *typed("one"), ESCAPE), *(TAB, ENTER, *typed("two\nmore"), ESCAPE), *keys(Key.Home)]
"""Comment #1 on removed line 11 and #2 on removed line 41, with the cursor back on the summary."""


async def test_hash_then_a_number_and_enter_goes_to_that_comment(harness: Harness) -> None:
    await harness.run([*TWO_COMMENTS, *keys("#"), *typed("1"), ENTER, harness.shot(), ENTER, *typed("!"), ESCAPE])

    assert [c.body for c in harness.review().comments] == ["one!", "two\nmore"]
    assert "Go to comment" not in harness.frames[0]


async def test_go_to_comment_previews_the_comment_the_number_names(harness: Harness) -> None:
    await harness.run([*TWO_COMMENTS, *keys("#"), harness.shot(), *typed("2"), harness.shot()])

    assert "Type a comment's number" in harness.frames[0]
    assert "#2 app.py, removed line 41" in harness.frames[1]
    # From the preview's title to the dialog's bottom border, since the comment itself shows in the diff below.
    preview = harness.frames[1].split("#2 app.py, removed line 41")[1].split("╰")[0]
    assert "two" in preview
    assert "more" not in preview


async def test_go_to_comment_says_when_no_comment_has_the_number_and_enter_stays(harness: Harness) -> None:
    await harness.run([*TWO_COMMENTS, *keys("#"), *typed("9"), ENTER, harness.shot(), ESCAPE, ENTER, *typed("s")])

    assert "No comment #9" in harness.frames[0]
    assert harness.review().summary == "s"


async def test_go_to_comment_takes_only_digits_and_keeps_keys_from_the_app(harness: Harness) -> None:
    await harness.run([*keys("#"), *typed("q#1x"), harness.shot()])

    assert "No comment #1" in harness.frames[0]


@pytest.mark.parametrize("leave", [ESCAPE, TAB], ids=["escape", "tab"])
async def test_leaving_go_to_comment_closes_it_where_the_cursor_was(harness: Harness, leave: KeyPressed) -> None:
    await harness.run([*TWO_COMMENTS, *keys("#"), *typed("2"), leave, harness.shot(), ENTER, *typed("s")])

    assert "Go to comment" not in harness.frames[0]
    assert harness.review().summary == "s"


async def test_clicking_the_previewed_comment_goes_to_it(harness: Harness) -> None:
    await harness.run([*TWO_COMMENTS, *keys("#"), *typed("2"), harness.shot()])
    previewed = where(harness.frames[0], "#2 app.py, removed line 41")

    await harness.run([*keys("#"), *typed("2"), *click(previewed.x, previewed.y), ENTER, *typed("!"), ESCAPE])

    assert [c.body for c in harness.review().comments] == ["one", "two\nmore!"]


async def test_hash_with_go_to_comment_left_open_carries_on_with_what_was_typed(harness: Harness) -> None:
    await harness.run([*TWO_COMMENTS, harness.shot()])
    row = where(harness.frames[0], "def f3():")

    await harness.run([*keys("#"), *typed("1"), *click(row.x, row.y), *keys("#"), ENTER, ENTER, *typed("!"), ESCAPE])

    assert [c.body for c in harness.review().comments] == ["one!", "two\nmore"]


async def test_shift_tab_goes_back_to_the_previous_stop(harness: Harness) -> None:
    await harness.run([TAB, TAB, KeyPressed(key=Key.BackTab), ENTER, *typed("back"), ESCAPE])

    assert lines(harness.review().comments[0]).start == 11


async def test_d_twice_deletes_the_comment_under_the_cursor(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("doomed"), ESCAPE, *keys("d"), harness.shot(), *keys("d"), harness.shot()])

    assert harness.review().comments == ()
    assert "press d again to delete #1" in harness.frames[0]
    assert "press d again" not in harness.frames[1]


@pytest.mark.parametrize("other", ["x", Key.Right, Key.Left, "h", "l", "0"])
async def test_d_then_another_key_keeps_the_comment(harness: Harness, other: str) -> None:
    await harness.run([TAB, ENTER, *typed("kept"), ESCAPE, *keys("d", other, "d"), harness.shot()])

    assert [c.body for c in harness.review().comments] == ["kept"]
    assert "press d again to delete #1" in harness.frames[0]


async def test_d_then_choosing_a_file_in_the_picker_keeps_the_comment(harness: Harness) -> None:
    await harness.run([harness.shot()])
    entry = where(harness.frames[0], "? notes.md")

    await harness.run([TAB, ENTER, *typed("kept"), ESCAPE, *keys("d"), *click(entry.x, entry.y), harness.shot()])

    assert "press d again" not in harness.frames[1]


async def test_r_edits_the_summary(harness: Harness) -> None:
    await harness.run([*keys("r"), *typed("Overall fine."), ESCAPE, harness.shot()])

    assert harness.review().summary == "Overall fine."
    assert "Overall fine." in harness.frames[0]


async def test_enter_on_a_comment_edits_it(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("a"), ESCAPE, ENTER, *typed("b"), ESCAPE])

    assert [c.body for c in harness.review().comments] == ["ab"]


async def test_enter_on_the_summary_edits_it(harness: Harness) -> None:
    await harness.run([ENTER, *typed("s"), ESCAPE])

    assert harness.review().summary == "s"


async def test_enter_on_a_file_header_comments_on_the_whole_file(harness: Harness) -> None:
    await harness.run([*keys(Key.Down), ENTER, *typed("split this up"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.anchor, made.excerpt, made.body) == (FileAnchor(path="app.py", side=None), (), "split this up")


async def test_escape_on_an_empty_file_comment_discards_it(harness: Harness) -> None:
    await harness.run([*keys(Key.Down), ENTER, ESCAPE, harness.shot()])

    assert harness.review().comments == ()
    assert "esc done" not in harness.frames[0]


async def test_s_switches_to_the_split_view_keeping_the_cursor_on_its_line(harness: Harness) -> None:
    await harness.run([TAB, *keys(Key.Down, "s"), harness.shot(), ENTER, ESCAPE])

    row = next(line for line in harness.frames[0].splitlines() if "return 6" in line)
    assert harness.preferences.load().view_mode is ViewMode.SPLIT
    assert row.index("11 -     return 3") < row.index("│ 11 +     return 6")
    assert harness.review().comments == ()


async def test_split_view_comments_anchor_to_the_working_tree(harness: Harness) -> None:
    await harness.run([*keys("s"), TAB, ENTER, *typed("split"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).side, lines(made).start) == (Side.NEW, 11)


async def test_s_twice_returns_to_the_unified_view(harness: Harness) -> None:
    await harness.run([*keys("s", "s")])

    assert harness.preferences.load().view_mode is ViewMode.UNIFIED


def branch_with_commits(harness: Harness, *subjects: str) -> list[str]:
    """Puts `feature` off `main`, with an empty commit for each of `subjects`, and returns their hashes, oldest first."""
    harness.scratch.git("checkout", "--quiet", "-b", "feature")
    return [harness.scratch.empty_commit(subject) for subject in subjects]


async def test_b_lists_the_branch_back_to_the_merge_base_under_the_working_tree(harness: Harness) -> None:
    first, second = branch_with_commits(harness, "first on feature", "second on feature")

    await harness.run([*keys("b"), showing("merge base with main"), harness.shot()])

    rows = [line for line in harness.frames[0].splitlines() if "on feature" in line or "merge base with" in line]
    assert "working tree" in harness.frames[0]
    assert [second[:7] in rows[0], first[:7] in rows[1], "merge base with main" in rows[2]] == [True, True, True]
    assert ("HEAD" in rows[0], "current base" in rows[0], "● " in rows[0]) == (True, True, True)


async def test_moving_down_the_picker_draws_the_diff_down_to_the_highlighted_commit(harness: Harness) -> None:
    branch_with_commits(harness, "first on feature", "second on feature")

    await harness.run([*keys("b"), showing("merge base with main"), *keys(Key.Down, "j"), harness.shot()])

    rows = [line for line in harness.frames[0].splitlines() if "on feature" in line or "merge base with" in line]
    assert ["│ " in rows[0], "│ " in rows[1], "● " in rows[2]] == [True, True, True]


async def test_enter_on_the_merge_base_follows_it_and_remembers_it(harness: Harness) -> None:
    branch_with_commits(harness, "on feature")

    await harness.run(
        [*keys("b"), showing("merge base with main"), *keys(Key.Down), ENTER, showing("merge base"), harness.shot()]
    )

    assert "against main (merge base" in harness.frames[0]
    assert harness.review().base == FOLLOW_MERGE_BASE


async def test_enter_on_another_commit_pins_it(harness: Harness) -> None:
    first, _ = branch_with_commits(harness, "first on feature", "second on feature")

    await harness.run(
        [*keys("b"), showing("merge base with main"), *keys(Key.Down), ENTER, showing("against commit"), harness.shot()]
    )

    assert f"against commit {first[:7]}" in harness.frames[0]
    assert harness.review().base == pinned(first)


async def test_the_picker_stops_at_either_end_and_moves_back_up(harness: Harness) -> None:
    first, _ = branch_with_commits(harness, "first on feature", "second on feature")

    await harness.run(
        [*keys("b"), showing("merge base with main"), *keys(Key.Up, *[Key.Down] * 4), harness.shot(), *keys("k"), ENTER]
    )

    (row,) = (line for line in harness.frames[0].splitlines() if "● " in line)
    assert "merge base with main" in row
    assert harness.review().base == pinned(first)


async def test_a_pinned_base_stays_put_as_head_moves(harness: Harness) -> None:
    first, _ = branch_with_commits(harness, "first on feature", "second on feature")

    def commit_more(_: object) -> bool:
        harness.scratch.empty_commit("third on feature")
        return True

    await harness.run(
        [
            WaitFor(commit_more, timeout=timedelta(seconds=10)),
            *keys("b"),
            showing("third on feature"),
            harness.shot(),
        ],
        base=pinned(first),
    )

    (row,) = (line for line in harness.frames[0].splitlines() if "current base" in line)
    assert first[:7] in row


async def test_clicking_a_commit_in_the_picker_picks_it(harness: Harness) -> None:
    first, _ = branch_with_commits(harness, "first on feature", "second on feature")

    await harness.run([*keys("b"), showing("first on feature"), harness.shot()])
    row = where(harness.frames[0], "first on feature")

    await harness.run([*keys("b"), showing("first on feature"), *click(row.x, row.y)])

    assert harness.review().base == pinned(first)


async def test_escape_closes_the_picker_and_keeps_the_base(harness: Harness) -> None:
    branch_with_commits(harness, "on feature")

    await harness.run([*keys("b"), showing("merge base with main"), *keys(Key.Down), ESCAPE, harness.shot()])

    assert "Compare the working tree with" not in harness.frames[0]
    assert "against HEAD" in harness.frames[0]


async def test_tab_closes_the_picker(harness: Harness) -> None:
    await harness.run([*keys("b"), showing("Compare the working tree with"), TAB, harness.shot()])

    assert "Compare the working tree with" not in harness.frames[0]


async def test_the_picker_without_a_branch_to_merge_with_lists_heads_history(harness: Harness) -> None:
    earlier = harness.scratch.git("rev-parse", "HEAD").strip()
    harness.scratch.empty_commit("later on main")

    await harness.run([*keys("b"), showing("later on main"), harness.shot()], target=None)

    assert "merge base with" not in harness.frames[0]
    assert where(harness.frames[0], earlier[:7]).y == where(harness.frames[0], "later on main").y + 1


async def test_the_picker_says_why_it_cant_read_the_history(harness: Harness) -> None:
    await harness.run([*keys("b"), showing("failed"), harness.shot()], target="no-such-branch")

    assert "git merge-base HEAD no-such-branch failed" in harness.frames[0]


async def test_a_pinned_base_that_is_gone_says_so(harness: Harness) -> None:
    await harness.run([showing("isn't in the repository"), harness.shot()], base=pinned("f" * 40), loaded="")

    assert "The base commit fffffff isn't in the repository any more; b picks another" in harness.frames[0]


async def test_a_notice_is_centered(harness: Harness) -> None:
    await harness.run([*keys("c"), showing("Copied"), harness.shot()])

    (line,) = (line for line in harness.frames[0].splitlines() if "Copied review" in line)
    assert abs((len(line) - len(line.lstrip())) - (len(line) - len(line.rstrip()))) <= 1


async def test_a_git_failure_shows_as_a_notice(harness: Harness) -> None:
    await harness.run([showing("failed"), harness.shot()], target="no-such-branch", base=FOLLOW_MERGE_BASE, loaded="")

    assert "git merge-base HEAD no-such-branch failed" in harness.frames[0]


def code_row(harness: Harness, shot: int) -> str:
    # The first line of `app.py` shows only when the whole file does, so these tests take `whole_harness`.
    """The colors of the first line of `app.py`, which sits above the theme picker, in one of the screenshots."""
    y = where(harness.frames[shot], "def f0():").y
    return harness.colored[shot].splitlines()[y]


async def test_theme_picker_previews_the_highlighted_theme_and_keeps_the_chosen_one(whole_harness: Harness) -> None:
    harness = whole_harness
    await harness.run([harness.shot(), *keys("t"), harness.shot(), *typed("monokai"), harness.shot(), ENTER])

    assert code_row(harness, 1) == code_row(harness, 0)
    assert code_row(harness, 2) != code_row(harness, 0)
    assert harness.preferences.load().theme == "monokai"


async def test_theme_picker_escape_reverts_and_saves_nothing(whole_harness: Harness) -> None:
    harness = whole_harness
    saved = harness.preferences.path.read_text()
    await harness.run([harness.shot(), *keys("t", Key.Down, Key.Down, Key.Up), harness.shot(), ESCAPE, harness.shot()])

    assert code_row(harness, 1) != code_row(harness, 0)
    assert code_row(harness, 2) == code_row(harness, 0)
    assert harness.preferences.path.read_text() == saved


async def test_tab_out_of_the_theme_picker_closes_it_and_reverts(whole_harness: Harness) -> None:
    harness = whole_harness
    saved = harness.preferences.path.read_text()
    await harness.run([harness.shot(), *keys("t", Key.Down), harness.shot(), TAB, harness.shot()])

    assert "Syntax theme" in harness.frames[1]
    assert "Syntax theme" not in harness.frames[2]
    assert code_row(harness, 2) == code_row(harness, 0)
    assert harness.preferences.path.read_text() == saved


async def test_tab_out_of_the_summary_dialog_closes_it_and_keeps_the_summary(harness: Harness) -> None:
    await harness.run([*keys("r"), *typed("tabbed"), TAB, harness.shot()])

    assert harness.review().summary == "tabbed"
    assert "esc done" not in harness.frames[0]


async def test_tab_out_of_a_comment_finishes_editing_it(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("tabbed"), TAB, harness.shot()])

    assert [c.body for c in harness.review().comments] == ["tabbed"]
    assert "esc done" not in harness.frames[0]


async def test_clicking_the_summary_while_editing_it_keeps_editing(harness: Harness) -> None:
    await harness.run([harness.shot()])
    summary = where(harness.frames[0], "Review summary")

    await harness.run([*click(summary.x, summary.y), *click(summary.x, summary.y), *typed("kept"), ESCAPE])

    assert harness.review().summary == "kept"


async def test_t_with_the_theme_picker_left_open_returns_to_it(harness: Harness) -> None:
    await harness.run([harness.shot()])
    row = where(harness.frames[0], "def f3():")

    await harness.run([*keys("t"), *click(row.x, row.y), *keys("t"), *typed("monokai"), ENTER])

    assert harness.preferences.load().theme == "monokai"


ERASE_NAME = keys(*[Key.Backspace] * 20)
"""Enough Backspaces to empty the field of a new review's name, which is when it started."""


@pytest.mark.parametrize(("name", "kept"), [("  spaced  ", "spaced"), ("   ", "   ")])
async def test_e_renames_the_review_from_the_status_line(harness: Harness, name: str, kept: str) -> None:
    await harness.run([*keys("e"), *ERASE_NAME, *typed(name), ENTER, harness.shot()])

    assert harness.review().name == kept
    assert f" {kept} " in harness.frames[0].splitlines()[0]


@pytest.mark.parametrize("leave", [ESCAPE, TAB], ids=["escape", "tab"])
async def test_leaving_the_name_field_keeps_the_old_name(harness: Harness, leave: KeyPressed) -> None:
    await harness.run([*keys("e"), *typed("zzz"), harness.shot(), leave, harness.shot()])

    assert "zzz" in harness.frames[0].splitlines()[0]
    assert "zzz" not in harness.frames[1]
    assert not harness.store.path(REVIEW_ID).exists()


async def test_clicking_the_name_in_the_status_line_edits_it(harness: Harness) -> None:
    await harness.run([*click(len(" assess-diff ") + 1, 0), *ERASE_NAME, *typed("clicked"), ENTER])

    assert harness.review().name == "clicked"


async def test_e_with_the_name_field_left_open_carries_on_with_what_was_typed(harness: Harness) -> None:
    await harness.run([harness.shot()])
    row = where(harness.frames[0], "def f3():")

    await harness.run([*keys("e"), *ERASE_NAME, *typed("draft"), *click(row.x, row.y), *keys("e"), *typed("!"), ENTER])

    assert harness.review().name == "draft!"


async def test_theme_picker_says_when_nothing_matches(harness: Harness) -> None:
    await harness.run([*keys("t"), *typed("zzzz"), harness.shot(), ENTER, ESCAPE])

    assert "No theme matches" in harness.frames[0]
    assert not harness.preferences.path.exists()


async def test_file_picker_jumps_to_a_file(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Down, Key.Down, Key.Enter, Key.Down), ENTER, *typed("on notes"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).path, lines(made).start) == ("notes.md", 1)


async def test_file_picker_keys_stop_at_either_end(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Up, "j", "j", "j", "k", "j", Key.Enter, Key.Down), ENTER, *typed("x"), ESCAPE])

    assert harness.review().comments[0].anchor.path == "notes.md"


async def test_tab_from_the_file_picker_returns_to_the_diff(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Tab, Key.Tab), ENTER, *typed("x"), ESCAPE])

    assert lines(harness.review().comments[0]).start == 11


async def test_clicking_a_row_puts_the_cursor_there(harness: Harness) -> None:
    await harness.run([harness.shot()])
    row = where(harness.frames[0], "return 6")

    await harness.run([*click(row.x, row.y), ENTER, *typed("clicked"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).side, lines(made).start) == (Side.NEW, 11)


async def test_shift_clicking_extends_the_selection(harness: Harness) -> None:
    await harness.run([harness.shot()])
    start = where(harness.frames[0], "def f3")
    end = where(harness.frames[0], "def f4")

    await harness.run([*click(start.x, start.y), *click(end.x, end.y, shift=True), ENTER, ESCAPE, harness.shot()])

    assert harness.review().comments == ()
    await harness.run([*click(start.x, start.y), *click(end.x, end.y, shift=True), ENTER, *typed("r"), ESCAPE])
    (made,) = harness.review().comments
    assert (lines(made).side, lines(made).start, lines(made).end) == (Side.NEW, 10, 13)


async def test_clicking_a_comment_edits_it(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("first"), ESCAPE, harness.shot()])
    box = where(harness.frames[0], "first")

    await harness.run([*click(box.x, box.y), *typed(" more"), ESCAPE])

    assert harness.review().comments[0].body == "first more"


async def test_clicking_the_summary_edits_it(harness: Harness) -> None:
    await harness.run([harness.shot()])
    box = where(harness.frames[0], "Review summary")

    await harness.run([*click(box.x, box.y), *typed("clicked"), ESCAPE])

    assert harness.review().summary == "clicked"


async def test_clicking_a_header_then_down_reaches_its_first_row(harness: Harness) -> None:
    await harness.run([harness.shot()])
    header = where(harness.frames[0], " app.py  +2 -2")

    await harness.run([*click(header.x, header.y), *keys(Key.Down), ENTER, *typed("top"), ESCAPE])

    # Line 8 is the first that shows: three lines of context above line 11, the first hunk.
    assert lines(harness.review().comments[0]).start == 8


async def test_clicking_a_file_in_the_picker_jumps_to_it(harness: Harness) -> None:
    await harness.run([harness.shot()])
    entry = where(harness.frames[0], "? notes.md")

    await harness.run([*click(entry.x, entry.y), *keys(Key.Down), ENTER, *typed("x"), ESCAPE])

    assert harness.review().comments[0].anchor.path == "notes.md"


async def test_page_and_end_keys_move_far(harness: Harness) -> None:
    await harness.run([*keys(Key.End), ENTER, *typed("last"), ESCAPE, *keys(Key.Home, Key.PageDown, Key.PageUp)])

    assert harness.review().comments[0].anchor.path == "notes.md"
    assert lines(harness.review().comments[0]).start == 3


async def test_moving_down_from_the_top_of_a_scrolled_view_leaves_the_view_be(harness: Harness) -> None:
    # Far enough down that the view scrolls, then back up past its top, so the cursor's row is the top one in view.
    await harness.run(
        [*keys(*[Key.Down] * 40, *[Key.Up] * 32), harness.shot(), *keys(Key.Down, Key.Down), harness.shot()]
    )

    assert harness.frames[1] == harness.frames[0]


# Seven eighths of the pane, which is the screen less the status line and the three rows the key hints wrap to.
PAGE = int((HEIGHT - 4) * 0.875)


async def test_page_down_scrolls_the_view_by_seven_eighths_of_it(whole_harness: Harness) -> None:
    harness = whole_harness
    await harness.run([harness.shot(), *keys(Key.PageDown), harness.shot()])

    assert where(harness.frames[0], " 20  20 ").y - where(harness.frames[1], " 20  20 ").y == PAGE


async def test_page_up_scrolls_the_view_back_by_as_much(whole_harness: Harness) -> None:
    harness = whole_harness
    await harness.run([*keys(Key.PageDown), harness.shot(), *keys(Key.PageDown, Key.PageUp), harness.shot()])

    assert harness.frames[1] == harness.frames[0]


async def test_page_down_moves_the_cursor_as_far_as_the_view_scrolls(whole_harness: Harness) -> None:
    harness = whole_harness
    # From the summary, past the header and the rows, the removed line 11 among them.
    await harness.run([*keys(Key.PageDown), ENTER, *typed("x"), ESCAPE])

    assert lines(harness.review().comments[0]).start == PAGE - 2


async def test_the_diff_follows_a_file_written_while_it_is_open(harness: Harness) -> None:
    def write_later(_: object) -> bool:
        """Writes the file, the same content each time it's asked, then lets the next step wait for it to show."""
        harness.scratch.write("later.py", "appeared = True\n")
        return True

    write = WaitFor(write_later, timeout=timedelta(seconds=10))

    await harness.run([write, showing("later.py"), harness.shot()])

    assert "? later.py" in harness.frames[0]


async def test_a_refresh_that_succeeds_clears_the_git_failure_and_no_other_notice(harness: Harness) -> None:
    def make_the_branch(_: object) -> bool:
        harness.scratch.git("branch", "--force", "no-such-branch")
        harness.scratch.write("later.py", "appeared = True\n")
        return True

    repository = await find_repository(harness.scratch.root)
    # Drawn until the merge base fails, so there's a diff to copy.
    against_head = await compute_diff(repository, await resolve_base(repository, FOLLOW_HEAD, None))
    recovered = WaitFor(lambda frame: "failed" not in screen_text(frame), timeout=timedelta(seconds=10))
    await harness.run(
        [
            showing("failed"),
            *keys("c"),
            showing("Copied"),
            harness.shot(),
            WaitFor(make_the_branch, timeout=timedelta(seconds=10)),
            recovered,
            harness.shot(),
        ],
        target="no-such-branch",
        base=FOLLOW_MERGE_BASE,
        initial_diff=against_head,
        loaded="",
    )

    assert "git merge-base HEAD no-such-branch failed" in harness.frames[0]
    assert "failed" not in harness.frames[1]
    assert "Copied review" in harness.frames[1]


async def test_a_selection_is_kept_within_its_file_when_the_file_shrinks(harness: Harness) -> None:
    def shrink(_: object) -> bool:
        harness.scratch.write("notes.md", "# Notes\n")
        return True

    await harness.run(
        [
            KeyPressed(key=Key.End),
            *keys("K", "K"),
            WaitFor(shrink, timeout=timedelta(seconds=10)),
            showing("+1 -0"),
            ENTER,
            *typed("x"),
            ESCAPE,
        ]
    )

    (made,) = harness.review().comments
    assert (lines(made).path, lines(made).start, lines(made).end) == ("notes.md", 1, 1)


async def test_up_moves_the_cursor_back(harness: Harness) -> None:
    await harness.run([TAB, *keys(Key.Down, Key.Down, "k", Key.Up), ENTER, *typed("x"), ESCAPE])

    assert (lines(harness.review().comments[0]).side, lines(harness.review().comments[0]).start) == (Side.OLD, 11)


async def test_a_commit_in_a_linked_worktree_updates_its_head_diff(harness: Harness, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    harness.scratch.git("stash", "--include-untracked", "--quiet")
    harness.scratch.git("worktree", "add", "--quiet", "-b", "feature", str(linked))
    worktree = Scratch(linked)
    worktree.write("app.py", functions({5: 50}))
    in_linked = Harness(scratch=worktree, store=harness.store, preferences=harness.preferences)

    def commit(_: object) -> bool:
        worktree.git("commit", "--quiet", "-am", "fifty")
        return True

    await in_linked.run(
        [showing("+1 -1"), WaitFor(commit, timeout=timedelta(seconds=10)), showing("0 files"), in_linked.shot()]
    )

    assert "0 files" in in_linked.frames[0]


async def test_status_line_counts_files_and_comments(harness: Harness) -> None:
    await harness.run([harness.shot(), TAB, ENTER, *typed("x"), ESCAPE, harness.shot()])

    assert " 2 files +5 -2   0 comments   unified " in harness.frames[0]
    assert " 1 comment   unified " in harness.frames[1]


async def test_comments_on_files_that_left_the_diff_are_drawn_last(harness: Harness) -> None:
    def with_orphan(opened: OpenReview) -> OpenReview:
        orphan = comment(1, "gone.py", 1, 1, ((LineKind.ADDED, "x"),), body="where did it go")
        opened.update(lambda r: r.model_copy(update={"comments": (orphan,), "next_number": 2}))
        return opened

    await harness.run([*keys(Key.End), harness.shot()], open_review=with_orphan)

    assert "Comments on files no longer in the diff" in harness.frames[0]
    assert "where did it go" in harness.frames[0]


async def test_outdated_comments_say_so(harness: Harness) -> None:
    def with_stale(opened: OpenReview) -> OpenReview:
        stale = comment(1, "app.py", 11, 11, ((LineKind.ADDED, "    return 7"),), body="stale")
        opened.update(lambda r: r.model_copy(update={"comments": (stale,), "next_number": 2}))
        return opened

    await harness.run([TAB, TAB, harness.shot()], open_review=with_stale)

    assert "╭ #1 outdated ─" in harness.frames[0]


async def test_a_review_changed_by_another_process_is_saved_as_a_new_one(harness: Harness) -> None:
    def changed_underneath(opened: OpenReview) -> OpenReview:
        opened.update(lambda r: edit_summary(r, lambda _: "mine"))
        harness.store.path(opened.review.id).write_text(
            opened.review.model_copy(update={"summary": "theirs"}).model_dump_json()
        )
        return opened

    await harness.run(
        [*keys("r"), *typed("!"), ESCAPE, showing("saved as forked"), harness.shot()], open_review=changed_underneath
    )

    assert harness.store.load("forked").review.summary == "mine!"
    assert "so this one is saved as forked" in harness.frames[0]


async def test_q_quits(harness: Harness) -> None:
    await harness.run([], quit_with=KeyPressed(key="q"))


async def test_a_binary_file_shows_its_placeholder(harness: Harness) -> None:
    harness.scratch.write("image.dat", b"\0\1\2")

    await harness.run([*keys("f", Key.Down, Key.Down, Key.Enter), showing("Binary file"), harness.shot()])

    assert "Binary file" in harness.frames[0]


def test_copying_before_the_diff_loads_says_so() -> None:
    assert copied(review(), None) == ("The diff is still loading", None)


def test_copying_cites_the_diff() -> None:
    document = build_document(diff_of(), (), ViewMode.UNIFIED, Scope.WHOLE)

    notice, control = copied(review(), document)

    assert notice == f"Copied review “{review().name}” to the clipboard as Markdown"
    assert control == CopyToClipboard(to_markdown(review(), diff_of()))


def status_groups(branch: str, against: str, *counts: tuple[Chunk, ...]) -> tuple[tuple[Chunk, ...], ...]:
    return (
        (Chunk(content=branch, style=text_bold),),
        (Chunk(content=against, style=text_color("slate", 300)),),
        *counts,
    )


def files_group(files: str, additions: str, deletions: str) -> tuple[Chunk, ...]:
    return (
        Chunk(content=files, style=text_color("slate", 400)),
        Chunk(content=additions, style=text_color("green", 400)),
        Chunk(content=deletions, style=text_color("red", 400)),
    )


def quiet(content: str) -> tuple[Chunk, ...]:
    return (Chunk(content=content, style=text_color("slate", 400)),)


FIRST = comment(1, "a.py", 2, 2, ((LineKind.ADDED, "two"),))
SECOND = comment(2, "b.py", 5, 5, ((LineKind.REMOVED, "five"),), side=Side.OLD)
CHANGED_FILES = (
    modified("a.py", removed(2, "zwei"), added(2, "two")),
    modified("b.py", removed(5, "five")),
)


@pytest.mark.parametrize(
    ("reviewed", "diff", "mode", "expected"),
    [
        (
            review(branch=None),
            None,
            ViewMode.UNIFIED,
            status_groups("detached HEAD", "reading the diff…", quiet("0 comments"), quiet("unified")),
        ),
        (
            review(FIRST, branch="topic/one"),
            diff_of(CHANGED_FILES[0], mode=BaseMode.HEAD, label="HEAD"),
            ViewMode.SPLIT,
            status_groups(
                "topic/one",
                "against HEAD c0ffee0",
                files_group("1 file", " +1", " -1"),
                quiet("1 comment"),
                quiet("split"),
            ),
        ),
        (
            review(FIRST, SECOND, branch="feature/x"),
            diff_of(*CHANGED_FILES, mode=BaseMode.MERGE_BASE, label="trunk"),
            ViewMode.UNIFIED,
            status_groups(
                "feature/x",
                "against trunk (merge base c0ffee0)",
                files_group("2 files", " +1", " -2"),
                quiet("2 comments"),
                quiet("unified"),
            ),
        ),
        (
            review(branch="feature/x"),
            diff_of(mode=BaseMode.COMMIT, label="c0ffee0"),
            ViewMode.UNIFIED,
            status_groups(
                "feature/x",
                "against commit c0ffee0",
                files_group("0 files", " +0", " -0"),
                quiet("0 comments"),
                quiet("unified"),
            ),
        ),
    ],
    ids=["loading-detached", "head", "merge-base", "commit"],
)
def test_status_line_says_the_branch_the_base_and_the_counts(
    reviewed: Review, diff: Diff | None, mode: ViewMode, expected: tuple[tuple[Chunk, ...], ...]
) -> None:
    assert status_line(reviewed, diff, mode) == expected


HANDLE = FocusHandle()


def test_key_hints_list_the_diffs_keys() -> None:
    assert key_hints(INITIAL_SESSION, Scope.WHOLE, True) == (
        "↑↓←→ move",
        "⇧↑↓ select",
        "tab hunk/comment",
        "n/N comment",
        "# go to #",
        "enter comment",
        "dd delete",
        "r summary",
        "e rename",
        "s split",
        "z fold unchanged",
        "b base",
        "t theme",
        "f files",
        "F fold files",
        "o reviews",
        "c copy",
        "⇧? fold keys",
        "q quit",
    )


@pytest.mark.parametrize(
    ("scope", "hint"),
    [(Scope.CONTEXT, "z unfold unchanged"), (Scope.WHOLE, "z fold unchanged")],
)
def test_key_hints_say_z_does_the_opposite_of_the_scope_shown(scope: Scope, hint: str) -> None:
    hints = key_hints(INITIAL_SESSION, scope, True)

    assert [h for h in hints if h.startswith("z ")] == [hint]


@pytest.mark.parametrize(
    ("count", "notice"),
    [
        (0, None),
        (1, "1 review file can't be read; o lists it"),
        (2, "2 review files can't be read; o lists them"),
    ],
)
def test_unreadable_notice_counts_the_files_and_says_where_they_are(count: int, notice: str | None) -> None:
    files = tuple(Unreadable(Path(f"/data/{index}.json"), "broken") for index in range(count))

    assert unreadable_notice(files) == notice


async def test_the_app_starts_saying_a_review_file_cant_be_read(harness: Harness) -> None:
    harness.store.directory.mkdir(parents=True)
    (harness.store.directory / "garbled.json").write_text("not json")

    await harness.run([harness.shot()])

    assert "1 review file can't be read; o lists it" in harness.frames[0]


def test_key_hints_folded_list_none_of_the_diffs_keys() -> None:
    assert key_hints(INITIAL_SESSION, Scope.CONTEXT, False) == ()


@pytest.mark.parametrize(
    ("session", "hints"),
    [
        (replace(INITIAL_SESSION, naming=Naming(HANDLE, "draft")), ("enter rename", "esc keep the old name")),
        (replace(INITIAL_SESSION, jumping=Jumping(HANDLE, "4")), ("type a number", "enter go", "esc close")),
        (
            replace(INITIAL_SESSION, managing=Managing(HANDLE, Listing((), ()))),
            ("↑↓ choose", "enter open", "e rename", "dd delete", "esc close"),
        ),
        (
            replace(
                INITIAL_SESSION, picking_theme=PickingTheme(HANDLE, Query(text="mono", highlight=Highlight(index=2)))
            ),
            ("type to filter", "↑↓ preview", "enter keep", "esc revert"),
        ),
        (
            replace(INITIAL_SESSION, picking_base=HANDLE, editing=(3, HANDLE)),
            ("↑↓ choose", "enter compare with it", "esc close"),
        ),
        (
            replace(INITIAL_SESSION, editing=(3, HANDLE)),
            ("esc done",),
        ),
        (
            replace(INITIAL_SESSION, confirming_delete=7),
            ("press d again to delete #7", "anything else to keep it"),
        ),
    ],
    ids=["naming", "jumping", "managing", "picking-theme", "picking-base", "editing", "confirming-delete"],
)
def test_key_hints_list_the_keys_of_what_has_focus_even_folded(session: Session, hints: tuple[str, ...]) -> None:
    assert key_hints(session, Scope.CONTEXT, False) == hints


def test_pills_pad_each_group_on_its_background_a_bare_space_apart_keeping_its_words_together() -> None:
    green = text_color("green", 400)
    on_pill = text_bg("slate", 900)
    edge = Chunk(content="\N{NO-BREAK SPACE}", style=on_pill)

    assert pills(((Chunk(content="t theme"),), (Chunk(content="2 files"), Chunk(content=" +3", style=green)))) == (
        edge,
        Chunk(content="t\N{NO-BREAK SPACE}theme", style=on_pill),
        edge,
        Chunk(content=" "),
        edge,
        Chunk(content="2\N{NO-BREAK SPACE}files", style=on_pill),
        Chunk(content="\N{NO-BREAK SPACE}+3", style=green | on_pill),
        edge,
    )


def test_key_pills_draw_each_hint_in_slate_as_a_pill() -> None:
    assert key_pills(("q quit", "c copy")) == pills(
        (
            (Chunk(content="q quit", style=text_color("slate", 400)),),
            (Chunk(content="c copy", style=text_color("slate", 400)),),
        ),
        ON_SCREEN,
    )


async def test_the_footer_wraps_between_hints_rather_than_cutting_them_off(harness: Harness) -> None:
    await harness.run([harness.shot()])

    *_, first, middle, last = harness.frames[0].splitlines()
    assert ("enter comment" in first, "c copy" in middle, last.split()[-2:]) == (True, True, ["q", "quit"])


async def test_question_mark_folds_the_keys_away_and_back(harness: Harness) -> None:
    await harness.run([*keys("?"), harness.shot(), *keys("?"), harness.shot()])

    assert ["q quit" in frame for frame in harness.frames] == [False, True]


async def test_shift_f_folds_the_files_away_and_back(harness: Harness) -> None:
    await harness.run([*keys("F"), harness.shot(), *keys("F"), harness.shot()])

    assert ["? notes.md" in frame for frame in harness.frames] == [False, True]


async def test_f_unfolds_the_files_to_choose_one(harness: Harness) -> None:
    await harness.run([*keys("F", "f", Key.Down, Key.Down), ENTER, *keys(Key.Down), ENTER, *typed("on notes"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.anchor.path, made.body) == ("notes.md", "on notes")


async def test_writing_the_summary_keeps_the_cursor_where_it_was(harness: Harness) -> None:
    await harness.run(
        [TAB, TAB, *keys("r"), *typed("Overall fine."), harness.shot(), ESCAPE, ENTER, *typed("here"), ESCAPE]
    )

    assert harness.review().summary == "Overall fine."
    assert (lines(harness.review().comments[0]).start, harness.review().comments[0].body) == (41, "here")


async def test_the_summary_dialog_opens_over_the_diff(harness: Harness) -> None:
    await harness.run([TAB, TAB, *keys("r"), harness.shot(), ESCAPE, harness.shot()])

    assert harness.frames[0].count("esc done") == 2, "the dialog says it, as well as the footer"
    assert "return 26" in harness.frames[0], "the cursor's line stays in view above the dialog"
    assert "esc done" not in harness.frames[1]


@pytest.mark.parametrize(
    ("steps", "marked"),
    [((), "Summary"), ((TAB,), "app.py"), ((TAB, ENTER, *typed("x"), ESCAPE), "app.py")],
    ids=["summary", "file", "comment"],
)
async def test_the_files_picker_marks_where_the_cursor_is_on_the_header_background(
    harness: Harness, steps: tuple[KeyPressed, ...], marked: str
) -> None:
    await harness.run([*steps, harness.shot()])

    slate = "48;2;15;23;42"
    names = ("Summary", "app.py", "notes.md")
    picker_rows = {
        name: colored.split("│", 1)[0]
        for plain, colored in zip(harness.frames[0].splitlines(), harness.colored[0].splitlines(), strict=True)
        for name in names
        if name in plain.split("│", 1)[0]
    }
    assert {name: slate in row for name, row in picker_rows.items()} == {name: name == marked for name in names}


async def test_a_files_first_comment_leaves_the_diff_as_wide_as_it_was(harness: Harness) -> None:
    await harness.run([harness.shot(), TAB, ENTER, *typed("x"), ESCAPE, harness.shot()])

    before, after = (where(frame, "│").x for frame in harness.frames)
    assert "app.py ●1" in harness.frames[1]
    assert after == before, "the picker's border, the diff's left edge, stays put"


async def test_the_summary_title_is_on_its_boxs_border(harness: Harness) -> None:
    await harness.run([harness.shot()])

    assert "╭ Review summary ─" in harness.frames[0]


async def test_the_summary_dialog_title_is_on_its_border(harness: Harness) -> None:
    await harness.run([*keys("r"), harness.shot()])

    (dialog_top,) = [line for line in harness.frames[0].splitlines() if "Review summary" in line and "│" not in line]
    assert "╭ Review summary ─" in dialog_top


async def test_hunks_show_with_their_context_and_a_line_for_each_run_hidden(harness: Harness) -> None:
    await harness.run([harness.shot()])

    gaps = [line.split("│ ", 1)[1].strip() for line in harness.frames[0].splitlines() if "unchanged line" in line]
    assert (gaps, "def f0():" in harness.frames[0]) == (
        ["⋯ 7 unchanged lines", "⋯ 23 unchanged lines", "⋯ 46 unchanged lines"],
        False,
    )


async def test_z_shows_whole_files_then_hunks_again_and_keeps_the_choice(harness: Harness) -> None:
    await harness.run([*keys("z"), harness.shot()])
    whole = harness.preferences.load().scope
    await harness.run([*keys("z"), harness.shot()])

    assert (
        "def f0():" in harness.frames[0],
        "unchanged line" in harness.frames[0],
        whole,
        "unchanged line" in harness.frames[1],
        harness.preferences.load().scope,
    ) == (True, False, Scope.WHOLE, True, Scope.CONTEXT)


async def test_z_from_a_hidden_line_moves_the_cursor_to_the_nearest_line_shown(whole_harness: Harness) -> None:
    # Onto line 1, from the summary and past the header, which hunks and their context hide.
    await whole_harness.run([*keys(Key.Down, Key.Down, "z"), ENTER, *typed("near"), ESCAPE])

    assert lines(whole_harness.review().comments[0]).start == 8


async def test_z_keeps_the_cursor_on_its_line(harness: Harness) -> None:
    await harness.run([TAB, *keys("z"), ENTER, *typed("same"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).side, lines(made).start) == (Side.OLD, 11)


async def test_a_selection_across_hidden_lines_comments_on_them_too(harness: Harness) -> None:
    # From `return 6` (line 11) down past the 23 hidden lines to line 38.
    await harness.run([TAB, *keys(Key.Down, "J", "J", "J", "J"), ENTER, *typed("all of it"), ESCAPE])

    repository = await find_repository(harness.scratch.root)
    diff = await compute_diff(repository, await resolve_base(repository, FOLLOW_HEAD, "HEAD"))
    (made,) = harness.review().comments
    assert (lines(made).start, lines(made).end, len(made.excerpt), is_outdated(made, diff)) == (
        11,
        38,
        28,
        False,
    )


@pytest.mark.parametrize(
    "goes_away",
    [[*typed("doomed"), ESCAPE, *keys("d", "d")], [ESCAPE]],
    ids=["deleted", "left-empty"],
)
async def test_a_comment_going_away_leaves_the_cursor_on_its_line_as_the_rows_it_kept_fold(
    harness: Harness, goes_away: list[KeyPressed]
) -> None:
    # Line 38, three above the second hunk, whose comment keeps lines 35 to 37 in view while it lasts.
    await harness.run([TAB, TAB, *keys(Key.Up, Key.Up, Key.Up), ENTER, *goes_away, ENTER, *typed("here"), ESCAPE])

    (made,) = harness.review().comments
    assert (lines(made).side, lines(made).start) == (Side.NEW, 38)


async def test_a_file_whose_only_change_is_its_mode_shows_a_line_for_all_of_it(harness: Harness) -> None:
    (harness.scratch.root / "app.py").write_text(functions())
    harness.scratch.write("tool.sh", "echo one\necho two\n")
    harness.scratch.commit()
    (harness.scratch.root / "tool.sh").chmod(0o755)

    await harness.run([harness.shot()], loaded="tool.sh")

    assert "⋯ 2 unchanged lines" in harness.frames[0]
