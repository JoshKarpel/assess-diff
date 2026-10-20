"""
The diff pane: the review summary, then each file's header, rows and comments, in one scroll.

Each file's rows are drawn as a few `Text`s, one per run of rows drawn alike, rather than one per row,
which keeps the element count near the number of hunks and comments, since whole files are drawn.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from counterweight.components import Component
from counterweight.components import component
from counterweight.elements import Chunk
from counterweight.elements import Div
from counterweight.elements import Text
from counterweight.events import InputEvent
from counterweight.events import InputHandler
from counterweight.events import InputResponse
from counterweight.events import KeyPressed
from counterweight.events import MouseDown
from counterweight.events import Receiver
from counterweight.focus import ElementHandle
from counterweight.focus import FocusHandle
from counterweight.handlers import first
from counterweight.handlers import scroll_on_wheel
from counterweight.hooks import use_focus
from counterweight.hooks import use_memo
from counterweight.hooks import use_rects
from counterweight.hooks import use_ref
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles import Style
from counterweight.styles.utilities import *
from counterweight.widgets import TextFieldStyles
from counterweight.widgets import text_area

from assess_diff.diff import FileStatus
from assess_diff.navigation import CommentAt
from assess_diff.navigation import Document
from assess_diff.navigation import FileLayout
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import SummaryAt
from assess_diff.review import Comment
from assess_diff.ui.rows import Highlight
from assess_diff.ui.rows import digits_for
from assess_diff.ui.rows import row_chunks
from assess_diff.ui.syntax import syntax_lines

COMMENT_STYLES = TextFieldStyles(
    box=border | border_lightrounded | border_color("amber", 400) | pad_x(1), focused_box=border_color("amber", 300)
)


@dataclass(frozen=True, slots=True)
class Run:
    """Consecutive rows drawn alike, as one `Text`: rows `start` up to but not including `stop`."""

    start: int
    stop: int
    highlight: Highlight


ROWS_PER_BLOCK = 128
"""
Runs are cut at every multiple of this many rows as well, so a cursor move changes the text of only the block or two
it moves within, and layout measures only those again, rather than the whole file on either side of the cursor.
"""


def runs(
    row_count: int, breaks: frozenset[int], cursor: int | None, selected: tuple[int, int] | None
) -> tuple[Run, ...]:
    """
    A file's rows cut into runs: at the cursor's row, which is drawn on its own to carry the scroll target,
    at the edges of the selection, after each row in `breaks`, which has comments drawn after it,
    and at the end of each block of `ROWS_PER_BLOCK` rows.
    """

    def highlight(index: int) -> Highlight:
        if index == cursor:
            return Highlight.CURSOR
        if selected is not None and selected[0] <= index <= selected[1]:
            return Highlight.SELECTED
        return Highlight.NONE

    found: list[Run] = []
    start = 0
    for index in range(row_count):
        is_last = index == row_count - 1
        is_block_end = (index + 1) % ROWS_PER_BLOCK == 0
        # The cursor's row needs no cut of its own: no other row shares its highlight.
        ends = is_last or is_block_end or index in breaks
        if not ends and highlight(index + 1) == highlight(index):
            continue
        found.append(Run(start, index + 1, highlight(index)))
        start = index + 1
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Actions:
    """What the diff pane's elements do when pressed, made once by the app and passed down."""

    press_row: Callable[[str, int, bool], InputResponse]
    press_position: Callable[[Position], InputResponse]
    edit_comment: Callable[[int, Callable[[str], str]], None]
    edit_summary: Callable[[Callable[[str], str]], None]
    finish_editing: Callable[[], InputResponse]


def use_stable_actions(actions: Actions) -> Actions:
    """
    An `Actions` that's the same object on every render, calling through to the `actions` passed in the latest one.
    The app's handlers close over each render's state, so they're new every render,
    and handing them down as they are would make every `memo` component below re-render on every key.
    """
    latest = use_ref(actions)
    latest.current = actions

    # Each looks up `latest.current` when it's called: passing `latest.current.press_row` itself instead
    # would keep the first render's handler, and its state, for good.
    def press_row(path: str, index: int, extend: bool) -> InputResponse:
        return latest.current.press_row(path, index, extend)

    def press_position(position: Position) -> InputResponse:
        return latest.current.press_position(position)

    def edit_comment(number: int, edit: Callable[[str], str]) -> None:
        latest.current.edit_comment(number, edit)

    def edit_summary(edit: Callable[[str], str]) -> None:
        latest.current.edit_summary(edit)

    def finish_editing() -> InputResponse:
        return latest.current.finish_editing()

    stable, _ = use_state(
        lambda: Actions(
            press_row=press_row,
            press_position=press_position,
            edit_comment=edit_comment,
            edit_summary=edit_summary,
            finish_editing=finish_editing,
        )
    )
    return stable


def press_rows(path: str, start: int, actions: Actions) -> InputHandler:
    def handler(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case MouseDown(absolute=position, button=1, is_shift_held=extend):
                return actions.press_row(path, start + receiver.relative(position).y, extend)
            case _:
                return None

    return handler


def press(position: Position, actions: Actions) -> InputHandler:
    def handler(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case MouseDown(button=1):
                return actions.press_position(position)
            case _:
                return None

    return handler


def finish_on_escape(actions: Actions) -> InputHandler:
    def handler(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case KeyPressed(key=Key.Escape):
                return actions.finish_editing()
            case _:
                return None

    return handler


def editor(
    title: tuple[Chunk, ...],
    value: str,
    edit: Callable[[Callable[[str], str]], None],
    handle: FocusHandle,
    actions: Actions,
    style: Style,
) -> Div:
    return Div(
        style=col | style,
        on_input=finish_on_escape(actions),
        children=(
            Text(content=title, style=text_wrap_none),
            text_area(value, edit, handle=handle, enter_inserts_newline=True, styles=COMMENT_STYLES),
        ),
    )


def comment_title(comment: Comment, is_outdated: bool) -> tuple[Chunk, ...]:
    return (
        Chunk(content=f"#{comment.number}", style=text_bold | text_color("amber", 300)),
        *((Chunk(content="  outdated", style=text_color("slate", 400)),) if is_outdated else ()),
    )


@component(memo=True)
def comment_box(
    comment: Comment,
    is_outdated: bool,
    is_cursor: bool,
    editing: FocusHandle | None,
    cursor_element: ElementHandle,
    actions: Actions,
) -> Div | Text:
    margin = margin_left(4) | margin_y(0)
    title = comment_title(comment, is_outdated)
    if editing is not None:
        return editor(
            title,
            comment.body,
            lambda edit: actions.edit_comment(comment.number, edit),
            editing,
            actions,
            margin,
        )
    body = comment.body or "(empty: Enter to write it)"
    return Text(
        content=(*title, Chunk.newline(), Chunk(content=body, style=default if comment.body else text_dim)),
        style=margin
        | border
        | border_lightrounded
        | pad_x(1)
        | text_wrap_stable
        | (border_color("amber", 300) | text_bg("slate", 800) if is_cursor else border_color("amber", 700)),
        handle=cursor_element if is_cursor else None,
        on_input=press(CommentAt(comment.number), actions),
    )


def header_chunks(layout: FileLayout) -> tuple[Chunk, ...]:
    file = layout.file
    name = file.path if file.old_path is None else f"{file.old_path} → {file.path}"
    status = "" if file.status is FileStatus.MODIFIED else f" ({file.status})"
    return (
        Chunk(content=f" {name}", style=text_bold),
        Chunk(content=status, style=text_color("slate", 400)),
        Chunk(content=f"  +{file.additions}", style=text_color("green", 400)),
        Chunk(content=f" -{file.deletions}", style=text_color("red", 400)),
    )


@component(memo=True)
def file_view(
    layout: FileLayout,
    comments: tuple[Comment, ...],
    outdated: frozenset[int],
    cursor: Position | None,
    selected: tuple[int, int] | None,
    editing: tuple[int, FocusHandle] | None,
    theme: str,
    width: int,
    cursor_element: ElementHandle,
    actions: Actions,
) -> Div:
    """
    One file: its header, its rows, and the comments after them.
    `cursor` and `selected` are only given when they're in this file, so moving them elsewhere leaves it be.
    """
    file = layout.file
    path = file.path
    syntax = use_memo(lambda: syntax_lines(file, theme), (file, theme))
    digits = digits_for(file.lines)
    by_number = {comment.number: comment for comment in comments}

    def boxes(index: int) -> tuple[Component, ...]:
        return tuple(
            comment_box(
                by_number[number],
                number in outdated,
                cursor == CommentAt(number),
                editing[1] if editing is not None and editing[0] == number else None,
                cursor_element,
                actions,
            ).with_key(str(number))
            for number in layout.comments_after(index)
        )

    is_header = cursor == HeaderAt(path)
    children: list[Component | Text | Div] = [
        Text(
            content=header_chunks(layout),
            style=text_wrap_none | (text_bg("indigo", 800) if is_header else text_bg("slate", 800)),
            handle=cursor_element if is_header else None,
            on_input=press(HeaderAt(path), actions),
        ),
        *boxes(-1),
    ]
    if file.placeholder is not None:
        children.append(Text(content=f"  {file.placeholder}", style=text_color("slate", 500)))

    cursor_row = cursor.index if isinstance(cursor, RowAt) else None
    breaks = frozenset(index for index, _ in layout.placed)
    for run in runs(len(layout.rows), breaks, cursor_row, selected):
        lines: list[Chunk] = []
        for index in range(run.start, run.stop):
            if index > run.start:
                lines.append(Chunk.newline())
            lines.extend(row_chunks(layout.rows[index], syntax, digits, width, run.highlight))
        # Keyed by the row it starts at, so the node scroll anchoring holds still always starts at that row.
        # Unkeyed, the node at an index shows whichever run is there now: the cursor's run moving down a row
        # would read to anchoring as content moving, and scroll the view along with the cursor.
        children.append(
            rows_run(
                tuple(lines), path, run.start, cursor_element if run.highlight is Highlight.CURSOR else None, actions
            ).with_key(f"rows-{run.start}")
        )
        children.extend(boxes(run.stop - 1))
    return Div(style=col | margin_bottom(1), children=tuple(children))


@component
def rows_run(lines: tuple[Chunk, ...], path: str, start: int, handle: ElementHandle | None, actions: Actions) -> Text:
    """A run of a file's rows starting at row `start`, drawn alike, with `handle` if it's the cursor's row."""
    return Text(content=lines, style=text_wrap_none, handle=handle, on_input=press_rows(path, start, actions))


SUMMARY_TITLE = (Chunk(content="Review summary", style=text_bold | text_color("amber", 300)),)
SUMMARY_STYLES = TextFieldStyles(box=pad_x(1), focused_box=default)
# Docked at the bottom rather than centered, since moves center the cursor's line, which should stay in view.
docked = position_absolute | inset_x(2) | inset_bottom(2) | fit_content_height | z(1)


@component
def summary_box(summary: str, is_cursor: bool, cursor_element: ElementHandle, actions: Actions) -> Text:
    """The summary at the top of the scroll, to read; pressing it opens the summary dialog to write it."""
    body = summary or "(none yet: r to write one)"
    return Text(
        content=(*SUMMARY_TITLE, Chunk.newline(), Chunk(content=body, style=default if summary else text_dim)),
        style=border
        | border_lightrounded
        | pad_x(1)
        | margin_bottom(1)
        | text_wrap_stable
        | (border_color("amber", 300) | text_bg("slate", 800) if is_cursor else border_color("slate", 600)),
        handle=cursor_element if is_cursor else None,
        on_input=press(SummaryAt(), actions),
    )


@component
def summary_dialog(summary: str, handle: FocusHandle, actions: Actions) -> Div:
    """
    The summary, edited in a dialog over the diff, so writing it doesn't move the cursor or the scroll,
    and Escape returns to the same place.
    """
    return Div(
        style=docked | col | border | border_lightrounded | border_color("amber", 400) | text_bg("slate", 900),
        on_input=finish_on_escape(actions),
        children=(
            Text(content=SUMMARY_TITLE, style=pad_x(1) | text_wrap_none),
            text_area(
                summary,
                actions.edit_summary,
                handle=handle,
                enter_inserts_newline=True,
                styles=SUMMARY_STYLES,
                style=min_height(3) | max_height(16),
            ),
            Text(content=" esc done  (saved as you type)", style=text_wrap_none | text_color("slate", 400)),
        ),
    )


@component
def diff_pane(
    document: Document | None,
    summary: str,
    comments: tuple[Comment, ...],
    outdated: frozenset[int],
    cursor: Position,
    selection: RowAt | None,
    editing: tuple[int | None, FocusHandle] | None,
    theme: str,
    handle: FocusHandle,
    cursor_element: ElementHandle,
    actions: Actions,
    on_key: InputHandler,
) -> Div:
    """
    The scrolling pane of the whole review. `editing` is the number of the comment being edited,
    or `None` for the summary, which is edited in a dialog rather than here, with the editor's focus handle.
    """
    use_focus(handle=handle, auto_focus=True)
    rects = use_rects()
    width = rects.content.width
    pane = col | fill(1) | overflow_scroll | pad_x(1)

    top = summary_box(summary, cursor == SummaryAt(), cursor_element, actions)
    if document is None:
        return Div(style=pane, on_input=on_key, children=(top, Text(content="Reading the diff…", style=text_dim)))

    comment_editing = (editing[0], editing[1]) if editing is not None and editing[0] is not None else None
    by_path: dict[str, list[Comment]] = {}
    for comment in comments:
        by_path.setdefault(comment.anchor.path, []).append(comment)

    def in_file(path: str) -> tuple[Position | None, tuple[int, int] | None]:
        match cursor:
            case RowAt(path=row_path, index=index) if row_path == path:
                start = selection.index if selection is not None and selection.path == path else index
                return cursor, (min(start, index), max(start, index)) if selection is not None else None
            case HeaderAt(path=header_path) if header_path == path:
                return cursor, None
            case CommentAt(number=number) if any(comment.number == number for comment in by_path.get(path, ())):
                return cursor, None
            case _:
                return None, None

    files = []
    for layout in document.files:
        file_cursor, selected = in_file(layout.file.path)
        files.append(
            file_view(
                layout,
                tuple(by_path.get(layout.file.path, ())),
                outdated,
                file_cursor,
                selected,
                comment_editing,
                theme,
                width,
                cursor_element,
                actions,
            ).with_key(layout.file.path)
        )

    orphaned = {comment.number: comment for comment in comments if comment.number in document.orphans}
    orphans: tuple[Component | Text, ...] = ()
    if orphaned:
        orphans = (
            Text(content=" Comments on files no longer in the diff", style=text_bold | text_bg("slate", 800)),
            *(
                comment_box(
                    comment,
                    True,
                    cursor == CommentAt(number),
                    comment_editing[1] if comment_editing is not None and comment_editing[0] == number else None,
                    cursor_element,
                    actions,
                ).with_key(str(number))
                for number, comment in orphaned.items()
            ),
        )

    return Div(
        style=pane,
        on_input=first(on_key, scroll_on_wheel()),
        children=(top, *files, *orphans),
    )
