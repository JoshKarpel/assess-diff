"""The list of changed files on the left, which jumps the diff to a file on Enter or a click."""

from __future__ import annotations

from collections.abc import Callable
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
from counterweight.focus import ElementHandle
from counterweight.focus import FocusHandle
from counterweight.handlers import first
from counterweight.handlers import focus_receiver_on_press
from counterweight.handlers import on_click
from counterweight.handlers import scroll_on_wheel
from counterweight.hooks import use_focus
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles.utilities import *

from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus

STATUS_LETTERS = {
    FileStatus.ADDED: "A",
    FileStatus.DELETED: "D",
    FileStatus.MODIFIED: "M",
    FileStatus.RENAMED: "R",
    FileStatus.UNTRACKED: "?",
}
STATUS_COLORS = {
    FileStatus.ADDED: text_color("green", 400),
    FileStatus.DELETED: text_color("red", 400),
    FileStatus.MODIFIED: text_color("amber", 300),
    FileStatus.RENAMED: text_color("sky", 300),
    FileStatus.UNTRACKED: text_color("green", 300),
}


def file_entry(file: FileDiff, comments: int) -> tuple[Chunk, ...]:
    counts = f" +{file.additions} -{file.deletions}" + (f" #{comments}" if comments else "")
    return (
        Chunk(content=f"{STATUS_LETTERS[file.status]} ", style=STATUS_COLORS[file.status]),
        Chunk(content=file.path),
        Chunk(content=counts, style=text_color("slate", 500)),
    )


@component
def file_picker(
    files: tuple[FileDiff, ...],
    comment_counts: tuple[int, ...],
    current: str | None,
    handle: FocusHandle,
    jump: Callable[[str], InputResponse],
) -> Div:
    """The changed files, each with its status, its line counts, and how many comments it has."""
    focus = use_focus(handle=handle, in_focus_order=False)
    highlighted, set_highlighted = use_state(0)
    highlighted_entry, _ = use_state(ElementHandle)
    shown = min(highlighted, len(files) - 1)

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        in_view = ScrollIntoView(highlighted_entry, block="nearest")
        match event:
            case KeyPressed(key=Key.Down | "j") if files:
                set_highlighted(lambda index: min(index + 1, len(files) - 1))
                return in_view
            case KeyPressed(key=Key.Up | "k") if files:
                set_highlighted(lambda index: max(min(index, len(files) - 1) - 1, 0))
                return in_view
            case KeyPressed(key=Key.Enter) if files:
                return jump(files[shown].path)
            case _:
                return None

    return Div(
        style=col
        | length(36)
        | shrink(0)
        | overflow_y_scroll
        | border_right
        | border_color("indigo", 400 if focus.is_focused else 700),
        on_input=first(on_input, focus_receiver_on_press, scroll_on_wheel()),
        children=tuple(
            Text(
                content=file_entry(file, comments),
                style=text_wrap_none
                | pad_x(1)
                | (text_bg("indigo", 800) if focus.is_focused and index == shown else default)
                | (text_bold if file.path == current else default),
                handle=highlighted_entry if index == shown else None,
                on_input=on_click(partial(jump, file.path)),
            )
            for index, (file, comments) in enumerate(zip(files, comment_counts, strict=True))
        )
        or (Text(content="No changes", style=pad_x(1) | text_color("slate", 500)),),
    )
