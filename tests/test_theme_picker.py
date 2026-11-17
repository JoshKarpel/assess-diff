from __future__ import annotations

from counterweight.headless import Highlight

from assess_diff.ui.theme_picker import Query

THEMES = ("zone", "dune", "bones", "one")


def test_a_query_matches_the_typed_letters_in_order_with_anything_between_them() -> None:
    assert Query(text="de").matches(THEMES) == ("dune",)


def test_a_query_matches_the_exactly_named_option_first_and_keeps_the_rest_in_order() -> None:
    assert Query(text="ONE").matches(THEMES) == ("one", "zone", "bones")


def test_editing_a_query_to_the_same_text_keeps_the_highlight() -> None:
    query = Query(text="mono", highlight=Highlight(key="monokai", index=3))

    assert query.edited("mono") == query


def test_editing_a_query_to_new_text_highlights_the_first_match() -> None:
    assert Query(text="mono", highlight=Highlight(key="monokai", index=3)).edited("monok") == Query(text="monok")


def test_the_highlighted_option_is_the_highlights_among_the_matches() -> None:
    assert Query(text="o", highlight=Highlight(index=2)).highlighted(THEMES) == "one"


def test_nothing_is_highlighted_when_nothing_matches() -> None:
    assert Query(text="zz").highlighted(THEMES) is None


def test_moving_past_the_last_match_wraps_to_the_first() -> None:
    moved = Query(text="o", highlight=Highlight(index=2)).moved(THEMES, 1)

    assert moved == Query(text="o", highlight=Highlight(key="zone", index=0))
