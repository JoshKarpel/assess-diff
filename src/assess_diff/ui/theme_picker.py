"""
The syntax theme picker: a dialog over the app with a field that narrows the themes as you type,
previewing the highlighted one on the diff behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from functools import partial

from counterweight.components import component
from counterweight.controls import ScrollIntoView
from counterweight.elements import Chunk
from counterweight.elements import Div
from counterweight.elements import Text
from counterweight.events import InputEvent
from counterweight.events import InputResponse
from counterweight.events import KeyPressed
from counterweight.events import Receiver
from counterweight.events import response_controls
from counterweight.focus import ElementHandle
from counterweight.focus import FocusHandle
from counterweight.handlers import on_click
from counterweight.handlers import scroll_on_wheel
from counterweight.headless import Highlight
from counterweight.headless import subsequence_matches
from counterweight.headless import use_text_input
from counterweight.hooks import Editor
from counterweight.hooks import get_state
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles.utilities import *

SHOWN_ROWS = 12

dialog_box = position_absolute | inset(0) | margin_auto | fit_content_height | z(1)


@dataclass(frozen=True, slots=True)
class Query:
    """What's typed in a picker, and which of its matches is highlighted, which an edit puts back on the first."""

    text: str = ""
    highlight: Highlight = field(default_factory=Highlight)

    def edited(self, text: str) -> Query:
        return self if text == self.text else Query(text=text)

    def matches(self, options: tuple[str, ...]) -> tuple[str, ...]:
        """The options the picker shows: those holding the typed letters in order, the one the text names exactly first."""
        return subsequence_matches(self.text, options)

    def highlighted(self, options: tuple[str, ...]) -> str | None:
        """The match the picker highlights, or `None` when nothing matches."""
        shown = self.matches(options)
        index = self.highlight.index_in(shown)
        return None if index is None else shown[index]

    def moved(self, options: tuple[str, ...], by: int) -> Query:
        """The highlight `by` matches on, wrapping at the ends."""
        return replace(self, highlight=self.highlight.moved(self.matches(options), by))


@component
def theme_picker(
    themes: tuple[str, ...],
    query: Query,
    set_query: Editor[Query],
    handle: FocusHandle,
    choose: Callable[[str], InputResponse],
    cancel: Callable[[], InputResponse],
) -> Div:
    """
    The picker over `themes`, controlled by the app, which keeps `query` so it can preview the highlighted theme
    in the same render that highlights it.
    """
    highlighted_match, _ = use_state(ElementHandle)

    def edit(edit_text: Callable[[str], str]) -> str:
        return set_query(lambda q: q.edited(edit_text(q.text))).text

    field = use_text_input(query.text, edit, handle=handle)
    shown = query.matches(themes)
    highlighted = query.highlighted(themes)

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        in_view = ScrollIntoView(highlighted_match, block="nearest")
        match event:
            case KeyPressed(key=Key.Down) if shown:
                set_query(lambda q: q.moved(themes, 1))
            case KeyPressed(key=Key.Up) if shown:
                set_query(lambda q: q.moved(themes, -1))
            # Several keys can arrive before the next render, so Enter chooses what the latest of them left highlighted.
            case KeyPressed(key=Key.Enter) if (chosen := get_state(set_query).highlighted(themes)) is not None:
                return choose(chosen)
            case KeyPressed(key=Key.Escape):
                return cancel()
            case _:
                if (edited := response_controls(field.on_input(event, receiver))) is None:
                    return None
                return (*edited, in_view)
        return in_view

    cursor = (text_color("slate", 900) | text_bg("slate", 100)).text_style
    return Div(
        style=dialog_box
        | width(40)
        | col
        | border
        | border_lightrounded
        | border_color("indigo", 400)
        | text_bg("slate", 900),
        on_input=on_input,
        children=(
            Text(content="Syntax theme", style=text_bold | pad_x(1)),
            Text(
                content=(
                    Chunk(content="> ", style=text_color("indigo", 300)),
                    Chunk(content=field.before_cursor),
                    Chunk(content=field.at_cursor, style=cursor),
                    Chunk(content=field.after_cursor),
                ),
                style=pad_x(1) | text_wrap_none,
                on_input=field.on_press,
            ),
            Div(
                style=col | max_height(SHOWN_ROWS) | overflow_y_scroll,
                on_input=scroll_on_wheel(1),
                children=tuple(
                    Text(
                        content=theme,
                        style=pad_x(1) | (text_bg("indigo", 800) if theme == highlighted else default),
                        handle=highlighted_match if theme == highlighted else None,
                        on_input=on_click(partial(choose, theme)),
                    )
                    for theme in shown
                )
                or (Text(content="No theme matches", style=pad_x(1) | text_color("slate", 500)),),
            ),
        ),
    )
