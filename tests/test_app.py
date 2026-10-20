from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from counterweight.autopilot import WaitFor
from counterweight.controls import CopyToClipboard
from counterweight.events import KeyPressed
from counterweight.keys import Key

from assess_diff.diff import BaseMode
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.export import to_markdown
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.preferences import PreferencesFile
from assess_diff.review import ExcerptLine
from assess_diff.review import edit_summary
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewStore
from assess_diff.ui.app import copied
from tests.builders import comment
from tests.builders import diff_of
from tests.builders import review
from tests.conftest import Scratch
from tests.harness import HEIGHT
from tests.harness import REVIEW_ID
from tests.harness import Harness
from tests.harness import click
from tests.harness import keys
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


async def test_tab_reaches_the_first_hunk_and_enter_comments_on_it(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("why six?"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.number, made.anchor.side, made.anchor.start, made.anchor.end, made.body) == (
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
    assert (made.anchor.path, made.anchor.side, made.anchor.start, made.anchor.end) == ("app.py", Side.NEW, 11, 13)
    assert [line.kind for line in made.excerpt] == [LineKind.ADDED, LineKind.CONTEXT, LineKind.CONTEXT]


async def test_shift_arrows_extend_and_shrink_the_selection(harness: Harness) -> None:
    await harness.run([TAB, *keys(Key.Down, Key.ShiftDown, Key.ShiftDown, Key.ShiftUp), ENTER, ESCAPE])

    assert harness.review().comments == ()
    assert harness.review().next_number == 2


async def test_escape_clears_the_selection(harness: Harness) -> None:
    await harness.run([TAB, *keys("J", Key.Escape), ENTER, *typed("one line"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.anchor.side, made.anchor.start, made.anchor.end) == (Side.NEW, 11, 11)


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

    assert [(c.number, c.anchor.start, c.body) for c in harness.review().comments] == [(1, 11, "one!"), (2, 41, "two")]


async def test_shift_tab_goes_back_to_the_previous_stop(harness: Harness) -> None:
    await harness.run([TAB, TAB, KeyPressed(key=Key.BackTab), ENTER, *typed("back"), ESCAPE])

    assert harness.review().comments[0].anchor.start == 11


async def test_d_twice_deletes_the_comment_under_the_cursor(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("doomed"), ESCAPE, *keys("d"), harness.shot(), *keys("d"), harness.shot()])

    assert harness.review().comments == ()
    assert "press d again to delete #1" in harness.frames[0]
    assert "press d again" not in harness.frames[1]


async def test_d_then_another_key_keeps_the_comment(harness: Harness) -> None:
    await harness.run([TAB, ENTER, *typed("kept"), ESCAPE, *keys("d", "x", "d"), harness.shot()])

    assert [c.body for c in harness.review().comments] == ["kept"]
    assert "press d again to delete #1" in harness.frames[0]


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


async def test_enter_on_a_file_header_does_nothing(harness: Harness) -> None:
    await harness.run([*keys(Key.Down), ENTER, harness.shot()])

    assert not harness.store.path(REVIEW_ID).exists()
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
    assert (made.anchor.side, made.anchor.start) == (Side.NEW, 11)


async def test_s_twice_returns_to_the_unified_view(harness: Harness) -> None:
    await harness.run([*keys("s", "s")])

    assert harness.preferences.load().view_mode is ViewMode.UNIFIED


async def test_b_switches_to_the_merge_base_and_remembers_it(harness: Harness) -> None:
    harness.scratch.git("checkout", "--quiet", "-b", "feature")

    await harness.run([*keys("b"), showing("merge base"), harness.shot(), ENTER, *typed("x"), ESCAPE])

    assert "against main (merge base" in harness.frames[0]
    assert harness.review().base_mode is BaseMode.MERGE_BASE


async def test_b_without_a_branch_to_merge_with_says_so(harness: Harness) -> None:
    await harness.run([*keys("b"), harness.shot()], target=None)

    assert "No branch to take a merge base with; pass one with --base" in harness.frames[0]


async def test_a_git_failure_shows_as_a_notice(harness: Harness) -> None:
    await harness.run([*keys("b"), showing("failed"), harness.shot()], target="no-such-branch")

    assert "git merge-base HEAD no-such-branch failed" in harness.frames[0]


def code_row(harness: Harness, shot: int) -> str:
    """The colors of the first line of `app.py`, which sits above the theme picker, in one of the screenshots."""
    y = where(harness.frames[shot], "def f0():").y
    return harness.colored[shot].splitlines()[y]


async def test_theme_picker_previews_the_highlighted_theme_and_keeps_the_chosen_one(harness: Harness) -> None:
    await harness.run([harness.shot(), *keys("t"), harness.shot(), *typed("monokai"), harness.shot(), ENTER])

    assert code_row(harness, 1) == code_row(harness, 0)
    assert code_row(harness, 2) != code_row(harness, 0)
    assert harness.preferences.load().theme == "monokai"


async def test_theme_picker_escape_reverts_and_saves_nothing(harness: Harness) -> None:
    await harness.run([harness.shot(), *keys("t", Key.Down, Key.Down, Key.Up), harness.shot(), ESCAPE, harness.shot()])

    assert code_row(harness, 1) != code_row(harness, 0)
    assert code_row(harness, 2) == code_row(harness, 0)
    assert not harness.preferences.path.exists()


async def test_tab_out_of_the_theme_picker_closes_it_and_reverts(harness: Harness) -> None:
    await harness.run([harness.shot(), *keys("t", Key.Down), harness.shot(), TAB, harness.shot()])

    assert "Syntax theme" in harness.frames[1]
    assert "Syntax theme" not in harness.frames[2]
    assert code_row(harness, 2) == code_row(harness, 0)
    assert not harness.preferences.path.exists()


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
    row = where(harness.frames[0], "def f0():")

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
    row = where(harness.frames[0], "def f0():")

    await harness.run([*keys("e"), *ERASE_NAME, *typed("draft"), *click(row.x, row.y), *keys("e"), *typed("!"), ENTER])

    assert harness.review().name == "draft!"


async def test_theme_picker_says_when_nothing_matches(harness: Harness) -> None:
    await harness.run([*keys("t"), *typed("zzzz"), harness.shot(), ENTER, ESCAPE])

    assert "No theme matches" in harness.frames[0]
    assert not harness.preferences.path.exists()


async def test_file_picker_jumps_to_a_file(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Down, Key.Enter, Key.Down), ENTER, *typed("on notes"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.anchor.path, made.anchor.start) == ("notes.md", 1)


async def test_file_picker_keys_stop_at_either_end(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Up, "j", "j", "k", "j", Key.Enter, Key.Down), ENTER, *typed("x"), ESCAPE])

    assert harness.review().comments[0].anchor.path == "notes.md"


async def test_tab_from_the_file_picker_returns_to_the_diff(harness: Harness) -> None:
    await harness.run([*keys("f", Key.Tab, Key.Tab), ENTER, *typed("x"), ESCAPE])

    assert harness.review().comments[0].anchor.start == 11


async def test_clicking_a_row_puts_the_cursor_there(harness: Harness) -> None:
    await harness.run([harness.shot()])
    row = where(harness.frames[0], "return 6")

    await harness.run([*click(row.x, row.y), ENTER, *typed("clicked"), ESCAPE])

    (made,) = harness.review().comments
    assert (made.anchor.side, made.anchor.start) == (Side.NEW, 11)


async def test_shift_clicking_extends_the_selection(harness: Harness) -> None:
    await harness.run([harness.shot()])
    start = where(harness.frames[0], "def f3")
    end = where(harness.frames[0], "def f4")

    await harness.run([*click(start.x, start.y), *click(end.x, end.y, shift=True), ENTER, ESCAPE, harness.shot()])

    assert harness.review().comments == ()
    await harness.run([*click(start.x, start.y), *click(end.x, end.y, shift=True), ENTER, *typed("r"), ESCAPE])
    (made,) = harness.review().comments
    assert (made.anchor.side, made.anchor.start, made.anchor.end) == (Side.NEW, 10, 13)


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

    assert harness.review().comments[0].anchor.start == 1


async def test_clicking_a_file_in_the_picker_jumps_to_it(harness: Harness) -> None:
    await harness.run([harness.shot()])
    entry = where(harness.frames[0], "? notes.md")

    await harness.run([*click(entry.x, entry.y), *keys(Key.Down), ENTER, *typed("x"), ESCAPE])

    assert harness.review().comments[0].anchor.path == "notes.md"


async def test_page_and_end_keys_move_far(harness: Harness) -> None:
    await harness.run([*keys(Key.End), ENTER, *typed("last"), ESCAPE, *keys(Key.Home, Key.PageDown, Key.PageUp)])

    assert harness.review().comments[0].anchor.path == "notes.md"
    assert harness.review().comments[0].anchor.start == 3


async def test_moving_down_from_the_top_of_a_scrolled_view_leaves_the_view_be(harness: Harness) -> None:
    # Far enough down that the view scrolls, then back up past its top, so the cursor's row is the top one in view.
    await harness.run(
        [*keys(*[Key.Down] * 40, *[Key.Up] * 32), harness.shot(), *keys(Key.Down, Key.Down), harness.shot()]
    )

    assert harness.frames[1] == harness.frames[0]


# Seven eighths of the pane, which is the screen less the status line and the key hints.
PAGE = int((HEIGHT - 2) * 0.875)


async def test_page_down_scrolls_the_view_by_seven_eighths_of_it(harness: Harness) -> None:
    await harness.run([harness.shot(), *keys(Key.PageDown), harness.shot()])

    assert where(harness.frames[0], " 20  20 ").y - where(harness.frames[1], " 20  20 ").y == PAGE


async def test_page_up_scrolls_the_view_back_by_as_much(harness: Harness) -> None:
    await harness.run([*keys(Key.PageDown), harness.shot(), *keys(Key.PageDown, Key.PageUp), harness.shot()])

    assert harness.frames[1] == harness.frames[0]


async def test_page_down_moves_the_cursor_as_far_as_the_view_scrolls(harness: Harness) -> None:
    # From the summary, past the header and the rows, the removed line 11 among them.
    await harness.run([*keys(Key.PageDown), ENTER, *typed("x"), ESCAPE])

    assert harness.review().comments[0].anchor.start == PAGE - 2


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

    recovered = WaitFor(lambda frame: "failed" not in frame.text(ansi=False), timeout=timedelta(seconds=10))
    await harness.run(
        [
            *keys("b"),
            showing("failed"),
            *keys("c"),
            showing("Copied"),
            harness.shot(),
            WaitFor(make_the_branch, timeout=timedelta(seconds=10)),
            recovered,
            harness.shot(),
        ],
        target="no-such-branch",
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
    assert (made.anchor.path, made.anchor.start, made.anchor.end) == ("notes.md", 1, 1)


async def test_up_moves_the_cursor_back(harness: Harness) -> None:
    await harness.run([TAB, *keys(Key.Down, Key.Down, "k", Key.Up), ENTER, *typed("x"), ESCAPE])

    assert (harness.review().comments[0].anchor.side, harness.review().comments[0].anchor.start) == (Side.OLD, 11)


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

    assert "2 files  0 comments  unified" in harness.frames[0]
    assert "1 comment  unified" in harness.frames[1]


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

    assert "#1  outdated" in harness.frames[0]


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

    await harness.run([*keys("f", Key.Down, Key.Enter), showing("Binary file"), harness.shot()])

    assert "Binary file" in harness.frames[0]


def test_copying_before_the_diff_loads_says_so() -> None:
    assert copied(review(), None) == ("The diff is still loading", None)


def test_copying_cites_the_diff() -> None:
    document = build_document(diff_of(), (), ViewMode.UNIFIED)

    notice, control = copied(review(), document)

    assert notice == f"Copied review “{review().name}” to the clipboard as Markdown"
    assert control == CopyToClipboard(to_markdown(review(), diff_of()))


async def test_writing_the_summary_keeps_the_cursor_where_it_was(harness: Harness) -> None:
    await harness.run(
        [TAB, TAB, *keys("r"), *typed("Overall fine."), harness.shot(), ESCAPE, ENTER, *typed("here"), ESCAPE]
    )

    assert harness.review().summary == "Overall fine."
    assert (harness.review().comments[0].anchor.start, harness.review().comments[0].body) == (41, "here")


async def test_the_summary_dialog_opens_over_the_diff(harness: Harness) -> None:
    await harness.run([TAB, TAB, *keys("r"), harness.shot(), ESCAPE, harness.shot()])

    assert "esc done  (saved as you type)" in harness.frames[0]
    assert "return 26" in harness.frames[0], "the cursor's line stays in view above the dialog"
    assert "esc done  (saved as you type)" not in harness.frames[1]
