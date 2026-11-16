from __future__ import annotations

from datetime import UTC
from datetime import datetime

import pytest
from counterweight.elements import Chunk
from counterweight.styles.utilities import text_bold
from counterweight.styles.utilities import text_color
from counterweight.styles.utilities import text_dim

from assess_diff.git import Commit
from assess_diff.git import History
from assess_diff.review import FOLLOW_HEAD
from assess_diff.review import FOLLOW_MERGE_BASE
from assess_diff.review import pinned
from assess_diff.ui.base_picker import BaseOption
from assess_diff.ui.base_picker import base_options
from assess_diff.ui.base_picker import current_index
from assess_diff.ui.base_picker import gutter
from assess_diff.ui.base_picker import option_chunks

WHEN = datetime(2026, 5, 6, 7, 8, tzinfo=UTC)
HEAD, MIDDLE, FORK = (Commit(letter * 40, f"commit {letter}", WHEN) for letter in "abc")


def test_options_follow_head_and_the_merge_base_and_pin_the_rest() -> None:
    options = base_options(History(commits=(HEAD, MIDDLE, FORK), merge_base=FORK.commit), "trunk")

    assert options == (
        BaseOption(HEAD, FOLLOW_HEAD, ("HEAD",)),
        BaseOption(MIDDLE, pinned(MIDDLE.commit), ()),
        BaseOption(FORK, FOLLOW_MERGE_BASE, ("merge base with trunk",)),
    )


def test_head_that_is_the_merge_base_follows_head_and_says_both() -> None:
    (option, _) = base_options(History(commits=(HEAD, MIDDLE), merge_base=HEAD.commit), "trunk")

    assert option == BaseOption(HEAD, FOLLOW_HEAD, ("HEAD", "merge base with trunk"))


def test_options_without_a_merge_base_pin_all_but_head() -> None:
    options = base_options(History(commits=(HEAD, MIDDLE), merge_base=None), None)

    assert [option.choice for option in options] == [FOLLOW_HEAD, pinned(MIDDLE.commit)]


@pytest.mark.parametrize(
    ("current", "index"),
    [(HEAD.commit, 0), (FORK.commit, 2), ("f" * 40, None), (None, None)],
    ids=["head", "fork", "unlisted", "unknown"],
)
def test_current_index_is_the_row_of_the_diffs_base(current: str | None, index: int | None) -> None:
    options = base_options(History(commits=(HEAD, MIDDLE, FORK), merge_base=FORK.commit), "trunk")

    assert current_index(options, current) == index


@pytest.mark.parametrize(("index", "line"), [(0, "│ "), (1, "│ "), (2, "● "), (3, "  ")])
def test_the_gutter_runs_from_the_working_tree_down_to_the_highlighted_commit(index: int, line: str) -> None:
    assert gutter(index, 2) == line


def test_an_option_shows_its_hash_subject_tags_and_time() -> None:
    option = BaseOption(FORK, FOLLOW_MERGE_BASE, ("merge base with trunk",))
    committed = WHEN.astimezone().strftime("%Y-%m-%d %H:%M")

    assert option_chunks(option, 2, 1, True) == (
        Chunk(content="  ", style=text_color("amber", 300)),
        Chunk(content="ccccccc", style=text_color("sky", 300)),
        Chunk(content=" commit c"),
        Chunk(content="  merge base with trunk", style=text_color("amber", 300)),
        Chunk(content="  current base", style=text_bold | text_color("green", 400)),
        Chunk(content=f"  {committed}", style=text_dim),
    )


def test_an_option_that_is_not_the_current_base_does_not_say_so() -> None:
    option = BaseOption(MIDDLE, pinned(MIDDLE.commit), ())

    assert all("current" not in chunk.content for chunk in option_chunks(option, 1, 1, False))
