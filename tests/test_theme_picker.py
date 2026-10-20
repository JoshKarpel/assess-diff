from __future__ import annotations

from assess_diff.ui.theme_picker import Query
from assess_diff.ui.theme_picker import matches


def test_matches_put_the_exactly_named_theme_first_and_keep_the_rest_in_order() -> None:
    assert matches("ONE", ("zone", "dune", "bones", "one")) == ("one", "zone", "bones")


def test_editing_a_query_to_the_same_text_keeps_the_highlight() -> None:
    assert Query(text="mono", highlighted=3).edited("mono") == Query(text="mono", highlighted=3)


def test_editing_a_query_to_new_text_highlights_the_first_match() -> None:
    assert Query(text="mono", highlighted=3).edited("monok") == Query(text="monok", highlighted=0)
