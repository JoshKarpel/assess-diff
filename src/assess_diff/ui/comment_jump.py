"""
The dialog `#` opens to go to a comment by its number, as one comment refers to another (`see #3`),
showing where the comment the typed number names is before Enter goes there.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import assert_never

from counterweight.components import component
from counterweight.elements import Chunk
from counterweight.elements import Div
from counterweight.elements import Text
from counterweight.events import InputEvent
from counterweight.events import InputResponse
from counterweight.events import KeyPressed
from counterweight.events import Receiver
from counterweight.focus import FocusHandle
from counterweight.handlers import on_click
from counterweight.headless import use_text_input
from counterweight.keys import Key
from counterweight.styles.utilities import *

from assess_diff.export import line_range
from assess_diff.review import Anchor
from assess_diff.review import Comment
from assess_diff.review import FileAnchor
from assess_diff.review import NewAnchor
from assess_diff.review import OldAnchor
from assess_diff.review import Review
from assess_diff.ui.theme_picker import dialog_box


def wanted(number: str, review: Review) -> Comment | None:
    """The comment `number` names, or `None` while nothing is typed or no comment has that number."""
    return review.comment(int(number)) if number else None


def place(anchor: Anchor) -> str:
    """Where a comment points, as the dialog says it: the export's reference, without its Markdown or the base."""
    match anchor:
        case NewAnchor(start=start, end=end):
            return f"{anchor.path}:{line_range(start, end)}"
        case OldAnchor(start=start, end=end):
            line_word = "line" if start == end else "lines"
            return f"{anchor.path}, removed {line_word} {line_range(start, end)}"
        case FileAnchor():
            return anchor.path
        case _ as unreachable:
            assert_never(unreachable)


def preview(number: str, found: Comment | None) -> tuple[Chunk, ...]:
    """The comment Enter would go to, with the first line of what it says, or why there's none."""
    if found is not None:
        first_line = found.body.strip().partition("\n")[0]
        return (
            Chunk(content=f"#{found.number}", style=text_bold | text_color("amber", 300)),
            Chunk(content=f" {place(found.anchor)}", style=text_color("slate", 300)),
            Chunk.newline(),
            Chunk(content=first_line or "(empty)", style=default if first_line else text_dim),
        )
    hint = f"No comment #{number}" if number else "Type a comment's number"
    return (Chunk(content=hint, style=text_color("slate", 500)),)


@component
def comment_jump(
    number: str,
    set_number: Callable[[Callable[[str], str]], None],
    handle: FocusHandle,
    found: Comment | None,
    go: Callable[[int], InputResponse],
    cancel: Callable[[], InputResponse],
) -> Div:
    """
    The dialog over the app, controlled by the app, which keeps what's typed so `#` returns to it,
    and looks up the comment it names as `found`. Only digits can be typed.
    """

    def only_digits(edit: Callable[[str], str]) -> None:
        set_number(lambda latest: new if (new := edit(latest)).isdigit() or not new else latest)

    field = use_text_input(number, only_digits, handle=handle)

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case KeyPressed(key=Key.Enter) if found is not None:
                return go(found.number)
            case KeyPressed(key=Key.Escape):
                return cancel()
            case _:
                return field.on_input(event, receiver)

    cursor = (text_color("slate", 900) | text_bg("slate", 100)).text_style
    return Div(
        style=dialog_box
        | width(50)
        | col
        | border
        | border_lightrounded
        | border_color("amber", 400)
        | text_bg("slate", 900),
        on_input=on_input,
        children=(
            Text(content="Go to comment", style=text_bold | pad_x(1)),
            Text(
                content=(
                    Chunk(content="#", style=text_color("amber", 300)),
                    Chunk(content=field.before_cursor),
                    Chunk(content=field.at_cursor, style=cursor),
                    Chunk(content=field.after_cursor),
                ),
                style=pad_x(1) | text_wrap_none,
                on_input=field.on_press,
            ),
            Text(
                content=preview(number, found),
                style=pad_x(1) | text_wrap_none,
                on_input=on_click(partial(go, found.number)) if found is not None else None,
            ),
        ),
    )
