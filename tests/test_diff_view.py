from __future__ import annotations

from assess_diff.ui.diff_view import ROWS_PER_BLOCK
from assess_diff.ui.diff_view import Run
from assess_diff.ui.diff_view import runs
from assess_diff.ui.rows import Highlight

NONE, CURSOR, SELECTED = Highlight.NONE, Highlight.CURSOR, Highlight.SELECTED
BLOCK = ROWS_PER_BLOCK


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


def test_runs_cut_after_a_row_with_comments_after_it() -> None:
    assert runs(11, frozenset({3}), None, None) == (Run(0, 4, NONE), Run(4, 11, NONE))
