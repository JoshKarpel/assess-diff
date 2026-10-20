"""
The changed files on the left, as a tree of their directories, which jumps the diff to a file on Enter or a click.
`/` narrows it to the files whose paths match what's typed, as a flat list.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from functools import partial
from typing import assert_never

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
from counterweight.events import response_controls
from counterweight.focus import ElementHandle
from counterweight.focus import FocusHandle
from counterweight.handlers import first
from counterweight.handlers import focus_receiver_on_press
from counterweight.handlers import on_click
from counterweight.handlers import scroll_on_wheel
from counterweight.headless import use_text_input
from counterweight.hooks import use_effect
from counterweight.hooks import use_focus
from counterweight.hooks import use_memo
from counterweight.hooks import use_ref
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles.utilities import *

from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.ui.theme_picker import Query
from assess_diff.ui.theme_picker import matches

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


@dataclass(frozen=True, slots=True)
class DirectoryRow:
    """
    A directory in the tree, with any directories that are all it holds compacted into its row:
    `path` is the whole path to the last of them, which names it for folding, and `label` the part this row adds.
    """

    path: str
    label: str
    depth: int
    is_folded: bool


@dataclass(frozen=True, slots=True)
class FileRow:
    """A file in the tree: `index` is its place among the files, and `label` its name."""

    index: int
    label: str
    depth: int


type TreeRow = DirectoryRow | FileRow


def tree_rows(paths: Sequence[str], folded: frozenset[str]) -> tuple[TreeRow, ...]:
    """
    The rows of the tree of `paths` showing, with the directories in `folded` closed:
    each directory before what it holds, a level deeper, and everything in the order its first file comes in `paths`.
    """
    return tuple(_rows(tuple((index, tuple(path.split("/"))) for index, path in enumerate(paths)), (), 0, folded))


def _rows(
    entries: tuple[tuple[int, tuple[str, ...]], ...], parent: tuple[str, ...], depth: int, folded: frozenset[str]
) -> Iterator[TreeRow]:
    """The rows for `entries`, each a file's index and its path's parts below `parent`."""
    # Keyed by the directory's name, or by the file's index, so each comes where its first file does.
    groups: dict[str | int, list[tuple[int, tuple[str, ...]]]] = {}
    for index, parts in entries:
        groups.setdefault(parts[0] if len(parts) > 1 else index, []).append((index, parts))
    for key, members in groups.items():
        if isinstance(key, int):
            ((index, (name,)),) = members
            yield FileRow(index=index, label=name, depth=depth)
            continue
        label: tuple[str, ...] = (key,)
        below = [(index, parts[1:]) for index, parts in members]
        # A directory holding only one directory is drawn on its row.
        while all(len(parts) > 1 for _, parts in below) and len({parts[0] for _, parts in below}) == 1:
            label = (*label, below[0][1][0])
            below = [(index, parts[1:]) for index, parts in below]
        path = "/".join((*parent, *label))
        is_folded = path in folded
        yield DirectoryRow(path=path, label="/".join(label), depth=depth, is_folded=is_folded)
        if not is_folded:
            yield from _rows(tuple(below), (*parent, *label), depth + 1, folded)


def file_entry(file: FileDiff, label: str, comments: int) -> tuple[Chunk, ...]:
    return (
        Chunk(content=f"{STATUS_LETTERS[file.status]} ", style=STATUS_COLORS[file.status]),
        Chunk(content=label),
        *((Chunk(content=f" #{comments}", style=text_color("slate", 500)),) if comments else ()),
    )


def directory_entry(directory: DirectoryRow) -> tuple[Chunk, ...]:
    return (
        Chunk(content="▸ " if directory.is_folded else "▾ ", style=text_color("slate", 500)),
        Chunk(content=f"{directory.label}/", style=text_color("sky", 300)),
    )


@dataclass(frozen=True, slots=True)
class Filtering:
    """The picker is narrowed to the files matching what's typed: its field's focus handle, and the query."""

    handle: FocusHandle
    query: Query


@component
def file_picker(
    files: tuple[FileDiff, ...],
    comment_counts: tuple[int, ...],
    current: str | None,
    handle: FocusHandle,
    jump: Callable[[str], InputResponse],
) -> Div:
    """
    The changed files as a tree, each with its status and how many comments it has, as wide as the widest of them.
    Enter or a press on a directory folds it or unfolds it, as Left and Right do.
    """
    focus = use_focus(handle=handle, in_focus_order=False)
    highlighted, set_highlighted = use_state(0)
    highlighted_entry, _ = use_state(ElementHandle)
    folded, set_folded = use_state(frozenset[str]())
    not_filtering: Filtering | None = None
    filtering, set_filtering = use_state(not_filtering)
    paths = use_memo(lambda: tuple(file.path for file in files), (files,))
    rows = use_memo(lambda: tree_rows(paths, folded), (paths, folded))
    shown = min(highlighted, len(rows) - 1)

    def toggle(directory: DirectoryRow, is_folded: bool) -> None:
        set_folded(lambda f: f | {directory.path} if is_folded else f - {directory.path})

    def press(index: int, row: TreeRow) -> InputResponse:
        set_highlighted(index)
        match row:
            case FileRow(index=file):
                return jump(files[file].path)
            case DirectoryRow():
                toggle(row, not row.is_folded)
                return FocusOn(handle)
            case _ as unreachable:
                assert_never(unreachable)

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        # Only the tree's own keys: those the filter's field leaves alone bubble past it.
        if not receiver.is_target or not rows:
            return None
        row = rows[shown]
        in_view = ScrollIntoView(highlighted_entry, block="nearest")
        match event:
            case KeyPressed(key=Key.Down | "j"):
                set_highlighted(lambda index: min(index + 1, len(rows) - 1))
                return in_view
            case KeyPressed(key=Key.Up | "k"):
                set_highlighted(lambda index: max(min(index, len(rows) - 1) - 1, 0))
                return in_view
            case KeyPressed(key=Key.Enter):
                return press(shown, row)
            case KeyPressed(key=Key.Left | "h") if isinstance(row, DirectoryRow):
                toggle(row, True)
                return ()
            case KeyPressed(key=Key.Right | "l") if isinstance(row, DirectoryRow):
                toggle(row, False)
                return ()
            case KeyPressed(key="/"):
                opened = Filtering(FocusHandle(), Query(text="", highlighted=0))
                set_filtering(opened)
                return FocusOn(opened.handle)
            case _:
                return None

    def entry(index: int, row: TreeRow) -> Text:
        match row:
            case FileRow(index=file_index):
                content = file_entry(files[file_index], row.label, comment_counts[file_index])
                is_current = files[file_index].path == current
            case DirectoryRow():
                content = directory_entry(row)
                is_current = False
            case _ as unreachable:
                assert_never(unreachable)
        return Text(
            content=(Chunk(content=" " * row.depth), *content),
            style=text_wrap_none
            | pad_x(1)
            # The current file on the header's and footer's background, under the highlight, which wins on its row.
            | (text_bold | text_bg("slate", 900) if is_current else default)
            | (text_bg("indigo", 800) if focus.is_focused and index == shown else default),
            handle=highlighted_entry if index == shown else None,
            on_input=on_click(partial(press, index, row)),
        )

    def set_query(edit: Callable[[Query], Query]) -> None:
        set_filtering(lambda f: f if f is None else replace(f, query=edit(f.query)))

    return Div(
        style=col
        | fit_content_width
        | shrink(0)
        | border_right
        | border_color("indigo", 400 if focus.is_focused or filtering is not None else 700),
        on_input=first(on_input, focus_receiver_on_press),
        children=(
            (
                filter_list(
                    files,
                    paths,
                    comment_counts,
                    filtering,
                    set_query,
                    lambda: set_filtering(None),
                    lambda: FocusOn(handle),
                    jump,
                )
                if filtering is not None
                else Div(
                    style=col | fill(1) | overflow_y_scroll,
                    on_input=scroll_on_wheel(),
                    children=tuple(entry(index, row) for index, row in enumerate(rows))
                    or (Text(content="No changes", style=pad_x(1) | text_color("slate", 500)),),
                )
            ),
        ),
    )


@component
def filter_list(
    files: tuple[FileDiff, ...],
    paths: tuple[str, ...],
    comment_counts: tuple[int, ...],
    filtering: Filtering,
    set_query: Callable[[Callable[[Query], Query]], None],
    dismiss: Callable[[], None],
    back: Callable[[], InputResponse],
    jump: Callable[[str], InputResponse],
) -> Div:
    """
    The files whose paths hold what's typed, matched as the theme picker matches themes:
    Enter jumps to the highlighted one, and Escape goes back to the tree.
    It closes once its field loses focus, however it loses it, so it's never left open with nothing driving it.
    """
    highlighted_match, _ = use_state(ElementHandle)
    has_been_focused = use_ref(False)
    query = filtering.query

    def edit(edit_text: Callable[[str], str]) -> None:
        set_query(lambda q: q.edited(edit_text(q.text)))

    field = use_text_input(query.text, edit, handle=filtering.handle)
    shown = matches(query.text, paths)
    index_of = {path: index for index, path in enumerate(paths)}

    # The field mounts unfocused and takes focus a render later, so only losing focus it had closes the list.
    # Noted as it renders rather than in the effect: an effect that a quick next key cancels before it starts never runs.
    if field.focus.is_focused:
        has_been_focused.current = True
    is_blurred = has_been_focused.current and not field.focus.is_focused

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        in_view = ScrollIntoView(highlighted_match, block="nearest")
        match event:
            case KeyPressed(key=Key.Down) if shown:
                set_query(lambda q: replace(q, highlighted=(q.highlighted + 1) % len(shown)))
            case KeyPressed(key=Key.Up) if shown:
                set_query(lambda q: replace(q, highlighted=(q.highlighted - 1) % len(shown)))
            case KeyPressed(key=Key.Enter) if shown:
                return jump(shown[query.highlighted])
            case KeyPressed(key=Key.Escape):
                dismiss()
                return back()
            case _:
                if (edited := response_controls(field.on_input(event, receiver))) is None:
                    return None
                return (*edited, in_view)
        return in_view

    cursor = (text_color("slate", 900) | text_bg("slate", 100)).text_style
    return Div(
        style=col | fill(1),
        on_input=on_input,
        children=(
            Text(
                content=(
                    Chunk(content="/ ", style=text_color("indigo", 300)),
                    Chunk(content=field.before_cursor),
                    Chunk(content=field.at_cursor, style=cursor),
                    Chunk(content=field.after_cursor),
                ),
                style=pad_x(1) | text_wrap_none,
                on_input=field.on_press,
            ),
            Div(
                style=col | fill(1) | overflow_y_scroll,
                on_input=scroll_on_wheel(),
                children=tuple(
                    Text(
                        content=file_entry(files[index_of[path]], path, comment_counts[index_of[path]]),
                        style=text_wrap_none
                        | pad_x(1)
                        | (text_bg("indigo", 800) if index == query.highlighted else default),
                        handle=highlighted_match if index == query.highlighted else None,
                        on_input=on_click(partial(jump, path)),
                    )
                    for index, path in enumerate(shown)
                )
                or (Text(content="No file matches", style=pad_x(1) | text_color("slate", 500)),),
            ),
            *((run_on_mount(dismiss),) if is_blurred else ()),
        ),
    )


@component
def run_on_mount(action: Callable[[], None]) -> Div:
    """
    Nothing on screen: runs `action` once, after the render that mounts it,
    so a parent can act on something only its render sees, such as having lost focus, by showing this.
    Its effect never reruns, so no later render cancels it before it starts.
    """

    async def run() -> None:
        action()

    use_effect(run, deps=())
    return Div()
