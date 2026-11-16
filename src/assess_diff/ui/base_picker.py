"""
The base picker: a dialog over the app listing the commits the working tree can be compared with,
newest first under the working tree, with a line down from the working tree to the highlighted commit,
so the two ends of the diff Enter would show are on the screen together.

`HEAD` and the merge base follow as they move, as the review's two original bases did;
any other commit is pinned by its hash, so the diff keeps showing everything since it as the agent commits.
"""

from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
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
from counterweight.handlers import on_click
from counterweight.handlers import scroll_on_wheel
from counterweight.headless import Highlight
from counterweight.hooks import get_state
from counterweight.hooks import use_effect
from counterweight.hooks import use_focus
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles.utilities import *

from assess_diff.git import Commit
from assess_diff.git import GitError
from assess_diff.git import History
from assess_diff.review import FOLLOW_HEAD
from assess_diff.review import FOLLOW_MERGE_BASE
from assess_diff.review import BaseChoice
from assess_diff.review import pinned
from assess_diff.ui.theme_picker import dialog_box

SHOWN_ROWS = 16


@dataclass(frozen=True, slots=True)
class BaseOption:
    """One commit the picker lists, the base choosing it gives the review, and what the reader knows it as."""

    commit: Commit
    choice: BaseChoice
    tags: tuple[str, ...]


def base_options(history: History, target: str | None) -> tuple[BaseOption, ...]:
    """
    The picker's rows: `HEAD` first, which follows `HEAD`, the merge base with `target`, which follows the merge base,
    and the rest, each pinned. When `HEAD` is the merge base, choosing it follows `HEAD`, which is the same diff.
    """

    def option(index: int, commit: Commit) -> BaseOption:
        is_head = index == 0
        is_merge_base = commit.commit == history.merge_base
        tags = (*(("HEAD",) if is_head else ()), *((f"merge base with {target}",) if is_merge_base else ()))
        if is_head:
            return BaseOption(commit=commit, choice=FOLLOW_HEAD, tags=tags)
        if is_merge_base:
            return BaseOption(commit=commit, choice=FOLLOW_MERGE_BASE, tags=tags)
        return BaseOption(commit=commit, choice=pinned(commit.commit), tags=tags)

    return tuple(option(index, commit) for index, commit in enumerate(history.commits))


def commit_of(option: BaseOption) -> str:
    """The key the highlight holds an option by: its commit's hash, which no other option shares."""
    return option.commit.commit


def gutter(index: int, highlighted: int) -> str:
    """The line from the working tree down to the highlighted commit: the commits the diff would include, and its base."""
    if index < highlighted:
        return "│ "
    if index == highlighted:
        return "● "
    return "  "


def option_chunks(option: BaseOption, index: int, highlighted: int, is_current: bool) -> tuple[Chunk, ...]:
    commit = option.commit
    committed = commit.committed.astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        Chunk(content=gutter(index, highlighted), style=text_color("amber", 300)),
        Chunk(content=commit.commit[:7], style=text_color("sky", 300)),
        Chunk(content=f" {commit.subject}"),
        *(Chunk(content=f"  {tag}", style=text_color("amber", 300)) for tag in option.tags),
        *((Chunk(content="  current base", style=text_bold | text_color("green", 400)),) if is_current else ()),
        Chunk(content=f"  {committed}", style=text_dim),
    )


@component
def base_picker(
    history: Callable[[], Awaitable[History]],
    target: str | None,
    current: str | None,
    handle: FocusHandle,
    choose: Callable[[BaseChoice], InputResponse],
    cancel: Callable[[], InputResponse],
) -> Div:
    """
    The picker, which reads `history` as it opens, so it lists the commits as they are then;
    `current` is the commit the diff is against now, which it starts highlighting, or `None` before there's a diff.
    """
    use_focus(handle=handle)
    # The rows once the history is read, or why it couldn't be.
    not_loaded: tuple[BaseOption, ...] | str | None = None
    loaded, set_loaded = use_state(not_loaded)
    # The highlight the reader moved, until which it's on the current base, wherever that is as `current` changes.
    not_moved: Highlight | None = None
    moved, set_moved = use_state(not_moved)
    highlighted_row, _ = use_state(ElementHandle)

    async def load() -> None:
        try:
            set_loaded(base_options(await history(), target))
        except GitError as error:
            set_loaded(str(error))

    use_effect(load, deps=())

    options = loaded if isinstance(loaded, tuple) else ()
    in_view = ScrollIntoView(highlighted_row, block="nearest")

    def or_current(highlight: Highlight | None) -> Highlight:
        return Highlight(key=current) if highlight is None else highlight

    def move(by: int) -> InputResponse:
        set_moved(lambda highlight: or_current(highlight).moved(options, by, commit_of, ends="stop"))
        return in_view

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case KeyPressed(key=Key.Down | "j") if options:
                return move(1)
            case KeyPressed(key=Key.Up | "k") if options:
                return move(-1)
            # Several keys can arrive before the next render, so Enter chooses where the latest move left the highlight.
            case KeyPressed(key=Key.Enter) if (
                index := or_current(get_state(set_moved)).index_in(options, commit_of)
            ) is not None:
                return choose(options[index].choice)
            case KeyPressed(key=Key.Escape):
                return cancel()
            case _:
                return None

    # `None` only before the history is read, since it always holds `HEAD`.
    highlighted = or_current(moved).index_in(options, commit_of)
    if isinstance(loaded, str):
        listed: tuple[Text, ...] = (Text(content=loaded, style=pad_x(1) | text_color("amber", 300)),)
    elif highlighted is None:
        listed = (Text(content="Reading the history…", style=pad_x(1) | text_color("slate", 500)),)
    else:
        listed = tuple(
            Text(
                content=option_chunks(option, index, highlighted, option.commit.commit == current),
                style=pad_x(1)
                | text_wrap_none
                | text_overflow_ellipsis
                | (text_bg("indigo", 800) if index == highlighted else default),
                handle=highlighted_row if index == highlighted else None,
                on_input=on_click(partial(choose, option.choice)),
            )
            for index, option in enumerate(options)
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
            Text(content="Compare the working tree with", style=text_bold | pad_x(1)),
            Text(
                content=(Chunk(content="◆ ", style=text_color("amber", 300)), Chunk(content="working tree")),
                style=pad_x(1) | text_wrap_none,
            ),
            Div(
                style=col | max_height(SHOWN_ROWS) | overflow_y_scroll,
                on_input=scroll_on_wheel(1),
                children=listed,
            ),
        ),
    )
