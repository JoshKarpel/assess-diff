"""
The review manager: a dialog over the app listing this project's reviews, this worktree's first,
to start a new review, open another, rename one, or delete one,
and under them the files in the store that can't be read as reviews.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from functools import partial

from counterweight.components import Component
from counterweight.components import component
from counterweight.controls import FocusOn
from counterweight.controls import ScrollIntoView
from counterweight.elements import Chunk
from counterweight.elements import Div
from counterweight.elements import Text
from counterweight.events import InputEvent
from counterweight.events import InputResponse
from counterweight.events import KeyPressed
from counterweight.events import Receiver
from counterweight.focus import ElementHandle
from counterweight.focus import FocusHandle
from counterweight.handlers import on_click
from counterweight.handlers import scroll_on_wheel
from counterweight.headless import Highlight
from counterweight.headless import use_text_input
from counterweight.hooks import Editor
from counterweight.hooks import get_state
from counterweight.hooks import use_focus
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles.utilities import *

from assess_diff.review import Review
from assess_diff.storage import Unreadable
from assess_diff.ui.pills import ON_DIALOG
from assess_diff.ui.pills import key_pills

SHOWN_ROWS = 14
SHOWN_UNREADABLE = 4

dialog_box = position_absolute | inset(0) | margin_auto | fit_content_height | z(1)


@dataclass(frozen=True, slots=True)
class ManagerActions:
    """What the dialog asks the app to do, each returning the controls to send with it."""

    start_new: Callable[[], InputResponse]
    open: Callable[[str], InputResponse]
    rename: Callable[[str, str], None]
    delete: Callable[[str], None]
    close: Callable[[], InputResponse]


@dataclass(frozen=True, slots=True)
class Renaming:
    review_id: str
    name: str
    handle: FocusHandle


NEW_REVIEW_KEY = object()
"""What the highlight holds "New review" by, which no review's id equals."""


def entry_key(review: Review | None) -> object:
    """
    What the highlight holds an entry by, so it stays on its review as the list changes around it:
    a review's id, which a rename keeps, or `NEW_REVIEW_KEY` for "New review", the `None` entry.
    """
    return NEW_REVIEW_KEY if review is None else review.id


@dataclass(frozen=True, slots=True)
class Choice:
    """Which entry is highlighted, and what's being renamed or confirmed."""

    highlight: Highlight
    renaming: Renaming | None
    confirming_delete: str | None


def entry_chunks(review: Review, is_open: bool, is_here: bool) -> tuple[Chunk, ...]:
    updated = review.updated.astimezone().strftime("%Y-%m-%d %H:%M")
    comments = f"{len(review.comments)} comment" + ("" if len(review.comments) == 1 else "s")
    return (
        Chunk(content="● " if is_open else ("  " if is_here else "↪ "), style=text_color("amber", 300)),
        Chunk(content=review.name, style=text_bold),
        Chunk(content=f"  {review.branch or 'detached HEAD'}  {comments}  updated {updated}", style=text_dim),
    )


def unreadable_chunks(file: Unreadable) -> tuple[Chunk, ...]:
    """
    A file that can't be read, by its name, and the first line of why,
    since a validation error goes on for a line per field; `assess-diff list` prints all of it.
    """
    return (
        Chunk(content="! ", style=text_color("red", 400)),
        Chunk(content=file.path.name),
        Chunk(content=f"  {file.reason.partition('\n')[0]}", style=text_dim),
    )


@component
def name_field(renaming: Renaming, set_name: Editor[str]) -> Text:
    field = use_text_input(renaming.name, set_name, handle=renaming.handle)
    cursor = (text_color("slate", 900) | text_bg("slate", 100)).text_style
    return Text(
        content=(
            Chunk(content="  name: ", style=text_color("amber", 300)),
            Chunk(content=field.before_cursor),
            Chunk(content=field.at_cursor, style=cursor),
            Chunk(content=field.after_cursor),
        ),
        style=text_wrap_none | text_bg("slate", 800),
        on_input=field.on_input,
    )


@component
def review_manager(
    reviews: tuple[Review, ...],
    unreadable: tuple[Unreadable, ...],
    open_id: str,
    worktree: str,
    handle: FocusHandle,
    actions: ManagerActions,
) -> Div:
    """
    `reviews` are this project's, this worktree's first; `open_id` is the one the app has open.
    `unreadable` are the files in the store that can't be read, whichever project they're of, since that can't be told.
    """
    use_focus(handle=handle)
    choice, set_choice = use_state(Choice(highlight=Highlight(), renaming=None, confirming_delete=None))
    highlighted_entry, _ = use_state(ElementHandle)
    entries = (None, *reviews)

    def highlighted_in(c: Choice) -> int:
        # `index_in` is `None` only with no entries, and "New review" is always there.
        return c.highlight.index_in(entries, entry_key) or 0

    highlighted = highlighted_in(choice)
    chosen = entries[highlighted]
    in_view = ScrollIntoView(highlighted_entry, block="nearest")

    def set_name(edit: Callable[[str], str]) -> str:
        settled = set_choice(
            lambda c: c if c.renaming is None else replace(c, renaming=replace(c.renaming, name=edit(c.renaming.name)))
        )
        # A closed field has nothing typed in it.
        return "" if settled.renaming is None else settled.renaming.name

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        # Several keys can arrive before the next render, so each key acts on the state the latest of them left.
        now = get_state(set_choice)
        latest = entries[highlighted_in(now)]
        renaming = now.renaming
        if renaming is not None:
            match event:
                case KeyPressed(key=Key.Enter):
                    actions.rename(renaming.review_id, renaming.name.strip() or renaming.name)
                    set_choice(lambda c: replace(c, renaming=None))
                    return FocusOn(handle)
                case KeyPressed(key=Key.Escape):
                    set_choice(lambda c: replace(c, renaming=None))
                    return FocusOn(handle)
                case _:
                    return None
        confirming = now.confirming_delete
        if confirming is not None and event != KeyPressed(key="d"):
            set_choice(lambda c: replace(c, confirming_delete=None))
        match event:
            case KeyPressed(key=Key.Down | "j"):
                set_choice(lambda c: replace(c, highlight=c.highlight.moved(entries, 1, entry_key, ends="stop")))
                return in_view
            case KeyPressed(key=Key.Up | "k"):
                set_choice(lambda c: replace(c, highlight=c.highlight.moved(entries, -1, entry_key, ends="stop")))
                return in_view
            case KeyPressed(key=Key.Enter):
                return actions.start_new() if latest is None else actions.open(latest.id)
            case KeyPressed(key="e") if latest is not None:
                rename_handle = FocusHandle()
                set_choice(lambda c: replace(c, renaming=Renaming(latest.id, latest.name, rename_handle)))
                return FocusOn(rename_handle)
            case KeyPressed(key="d") if latest is not None:
                if confirming == latest.id:
                    actions.delete(latest.id)
                    set_choice(lambda c: replace(c, confirming_delete=None))
                else:
                    set_choice(lambda c: replace(c, confirming_delete=latest.id))
                return ()
            case KeyPressed(key=Key.Escape):
                return actions.close()
            case _:
                return None

    def entry(index: int, review: Review | None) -> tuple[Text | Component, ...]:
        is_highlighted = index == highlighted
        style = (
            pad_x(1) | text_wrap_none | text_overflow_ellipsis | (text_bg("indigo", 800) if is_highlighted else default)
        )
        handle_for = highlighted_entry if is_highlighted else None
        if review is None:
            return (
                Text(
                    content=(Chunk(content="+ New review", style=text_bold | text_color("green", 400)),),
                    style=style,
                    handle=handle_for,
                    on_input=on_click(actions.start_new),
                ),
            )
        rows: list[Text | Component] = [
            Text(
                content=entry_chunks(review, review.id == open_id, str(review.worktree) == worktree),
                style=style,
                handle=handle_for,
                on_input=on_click(partial(actions.open, review.id)),
            )
        ]
        if choice.renaming is not None and choice.renaming.review_id == review.id:
            rows.append(name_field(choice.renaming, set_name))
        return tuple(rows)

    footer = (
        (f"press d again to delete “{chosen.name}”",)
        if chosen is not None and choice.confirming_delete == chosen.id
        else ("enter open", "e rename", "dd delete", "esc close", "● open", "↪ another worktree")
    )
    return Div(
        style=dialog_box
        | width(90)
        | col
        | border
        | border_lightrounded
        | border_color("amber", 400)
        | text_bg("slate", 900),
        on_input=on_input,
        children=(
            Text(content="Reviews", style=text_bold | pad_x(1)),
            Div(
                style=col | max_height(SHOWN_ROWS) | overflow_y_scroll,
                on_input=scroll_on_wheel(1),
                children=tuple(row for index, review in enumerate(entries) for row in entry(index, review)),
            ),
            *(
                (
                    Text(content="Can't read", style=text_bold | pad_x(1) | margin_top(1)),
                    Div(
                        style=col | max_height(SHOWN_UNREADABLE) | overflow_y_scroll,
                        on_input=scroll_on_wheel(1),
                        children=tuple(
                            Text(
                                content=unreadable_chunks(file),
                                style=pad_x(1) | text_wrap_none | text_overflow_ellipsis,
                            )
                            for file in unreadable
                        ),
                    ),
                )
                if unreadable
                else ()
            ),
            Text(content=key_pills(footer, ON_DIALOG), style=text_wrap_stable | pad_x(1) | text_bg("slate", 900)),
        ),
    )
