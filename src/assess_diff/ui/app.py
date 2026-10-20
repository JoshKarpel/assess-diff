"""
The assess-diff app's root component: the review, the diff it follows, the reader's preferences,
and the cursor, and how keys and presses change them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from counterweight.components import component
from counterweight.controls import CopyToClipboard
from counterweight.controls import FocusOn
from counterweight.controls import Quit
from counterweight.controls import ScrollAlignment
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
from counterweight.hooks import use_effect
from counterweight.hooks import use_memo
from counterweight.hooks import use_state
from counterweight.keys import Key
from counterweight.styles.utilities import *
from watchfiles import awatch

from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.export import to_markdown
from assess_diff.git import GitError
from assess_diff.git import Repository
from assess_diff.git import compute_diff
from assess_diff.git import is_watched
from assess_diff.git import relevant_batches
from assess_diff.git import resolve_base
from assess_diff.navigation import CommentAt
from assess_diff.navigation import Document
from assess_diff.navigation import HeaderAt
from assess_diff.navigation import Position
from assess_diff.navigation import RowAt
from assess_diff.navigation import SummaryAt
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.navigation import comment_row
from assess_diff.navigation import extended
from assess_diff.navigation import in_mode
from assess_diff.navigation import moved
from assess_diff.navigation import next_comment
from assess_diff.navigation import next_stop
from assess_diff.navigation import primary
from assess_diff.navigation import recovered
from assess_diff.navigation import row_lines
from assess_diff.navigation import selection_span
from assess_diff.preferences import Preferences
from assess_diff.preferences import PreferencesFile
from assess_diff.preferences import themes
from assess_diff.review import Review
from assess_diff.review import add_comment
from assess_diff.review import anchor_for
from assess_diff.review import delete_comment
from assess_diff.review import edit_comment
from assess_diff.review import edit_summary
from assess_diff.review import is_outdated
from assess_diff.review import rename
from assess_diff.storage import OpenReview
from assess_diff.storage import for_project
from assess_diff.ui.diff_view import Actions
from assess_diff.ui.diff_view import diff_pane
from assess_diff.ui.diff_view import summary_dialog
from assess_diff.ui.file_picker import file_picker
from assess_diff.ui.review_manager import ManagerActions
from assess_diff.ui.review_manager import review_manager
from assess_diff.ui.theme_picker import Query
from assess_diff.ui.theme_picker import highlighted_theme
from assess_diff.ui.theme_picker import theme_picker

# How long the watcher waits for a burst of changes to settle before the diff is recomputed.
DEBOUNCE_MILLISECONDS = 200

PAGE_POSITIONS = 20


@dataclass(frozen=True, slots=True)
class Session:
    """Where the reader is: the cursor, the selection's other end, and what they're editing or confirming."""

    cursor: Position
    selection: RowAt | None
    editing: tuple[int | None, FocusHandle] | None
    """The number of the comment being edited, or `None` for the summary, with its editor's focus handle."""
    confirming_delete: int | None
    picking_theme: PickingTheme | None
    managing: Managing | None


@dataclass(frozen=True, slots=True)
class PickingTheme:
    """The theme picker is open: its focus handle, and what's typed and highlighted in it."""

    handle: FocusHandle
    query: Query


@dataclass(frozen=True, slots=True)
class Managing:
    """The review manager is open: its focus handle, and the reviews it lists, read when it opened or last changed."""

    handle: FocusHandle
    reviews: tuple[Review, ...]


INITIAL_SESSION = Session(
    cursor=SummaryAt(), selection=None, editing=None, confirming_delete=None, picking_theme=None, managing=None
)


def other_mode(mode: ViewMode) -> ViewMode:
    return ViewMode.SPLIT if mode is ViewMode.UNIFIED else ViewMode.UNIFIED


def other_base(mode: BaseMode) -> BaseMode:
    return BaseMode.HEAD if mode is BaseMode.MERGE_BASE else BaseMode.MERGE_BASE


def copied(review: Review, document: Document | None) -> tuple[str, CopyToClipboard | None]:
    """The notice for `c`, and the control that copies the review as Markdown, once there's a diff to cite."""
    if document is None:
        return "The diff is still loading", None
    return (
        f"Copied review “{review.name}” to the clipboard as Markdown",
        CopyToClipboard(to_markdown(review, document.diff)),
    )


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def status_line(review: Review, diff: Diff | None, mode: ViewMode) -> tuple[Chunk, ...]:
    branch = review.branch or "detached HEAD"
    comments = len(review.comments)
    if diff is None:
        against = "reading the diff…"
    elif diff.base.mode is BaseMode.HEAD:
        against = f"against HEAD {diff.base.short}"
    else:
        against = f"against {diff.base.label} (merge base {diff.base.short})"
    files = "" if diff is None else plural(len(diff.files), "file")
    return (
        Chunk(content=" assess-diff ", style=text_bold | text_bg("indigo", 700)),
        Chunk(content=f" {review.name} ", style=text_bold | text_color("amber", 300)),
        Chunk(content=f" {branch} ", style=text_bold),
        Chunk(content=against, style=text_color("slate", 300)),
        Chunk(content=f"  {files}  {plural(comments, 'comment')}  {mode}", style=text_color("slate", 400)),
    )


def key_hints(session: Session) -> str:
    if session.managing is not None:
        return " ↑↓ choose  enter open  e rename  dd delete  esc close"
    if session.picking_theme is not None:
        return " type to filter  ↑↓ preview  enter keep  esc revert"
    if session.editing is not None:
        return " esc done  (everything you type is saved as you type it)"
    if session.confirming_delete is not None:
        return f" press d again to delete #{session.confirming_delete}, anything else to keep it"
    return " ↑↓ move  ⇧↑↓ select  tab hunk/comment  n/N comment  enter comment  dd delete  r summary  s split  b base  t theme  f files  o reviews  c copy  q quit"


@component
def assess_app(
    repository: Repository,
    target: str | None,
    open_review: OpenReview,
    preferences_file: PreferencesFile,
    initial_preferences: Preferences,
) -> Div:
    """
    The whole app. `target` is the branch the merge base is taken with,
    or `None` when there isn't one, which leaves the base at `HEAD`.
    """
    review, set_review = use_state(open_review.review)
    preferences, set_preferences = use_state(initial_preferences)
    no_diff: Diff | None = None
    no_notice: str | None = None
    base_mode, set_base_mode = use_state(review.base_mode if target is not None else BaseMode.HEAD)
    diff, set_diff = use_state(no_diff)
    session, set_session = use_state(INITIAL_SESSION)
    notice, set_notice = use_state(no_notice)
    diff_focus, _ = use_state(FocusHandle)
    picker_focus, _ = use_state(FocusHandle)
    cursor_element, _ = use_state(ElementHandle)

    async def follow_diff() -> None:
        async def refresh() -> None:
            try:
                base = await resolve_base(repository, base_mode, target or "HEAD")
                set_diff(await compute_diff(repository, base))
            except GitError as error:
                set_notice(str(error))

        await refresh()
        # A linked worktree's git directory is outside it, so it's watched beside it.
        paths: tuple[Path, ...] = (repository.worktree,)
        if not repository.git_dir.is_relative_to(repository.worktree):
            paths = (*paths, repository.git_dir)
        batches = awatch(
            *paths,
            watch_filter=lambda _, path: is_watched(repository.git_dir, Path(path)),
            debounce=DEBOUNCE_MILLISECONDS,
        )
        async for _ in relevant_batches(repository, batches):
            await refresh()

    use_effect(follow_diff, deps=(base_mode,))

    mode = preferences.view_mode
    picking = session.picking_theme
    theme = (highlighted_theme(themes(), picking.query) if picking is not None else None) or preferences.theme
    document = use_memo(
        lambda: None if diff is None else build_document(diff, review.comments, mode),
        (diff, review.comments, mode),
    )
    outdated = use_memo(
        lambda: frozenset(
            comment.number for comment in review.comments if diff is not None and is_outdated(comment, diff)
        ),
        (diff, review.comments),
    )
    cursor = SummaryAt() if document is None else recovered(document, session.cursor)

    def update_review(edit: Callable[[Review], Review]) -> None:
        if (saved_notice := open_review.update(edit)) is not None:
            set_notice(saved_notice)
        set_review(open_review.review)

    def start_editing(number: int | None) -> InputResponse:
        """Edit comment `number` where it's drawn, or with `None`, the summary in its dialog, leaving the cursor be."""
        handle = FocusHandle()
        if number is None:
            set_session(lambda s: replace(s, editing=(None, handle), confirming_delete=None))
            return FocusOn(handle)
        set_session(
            lambda s: replace(
                s, editing=(number, handle), cursor=CommentAt(number), selection=None, confirming_delete=None
            )
        )
        return FocusOn(handle), ScrollIntoView(cursor_element, block="nearest")

    def finish_editing() -> InputResponse:
        editing = session.editing
        set_session(lambda s: replace(s, editing=None))
        if editing is not None and editing[0] is not None:
            number = editing[0]
            comment = open_review.review.comment(number)
            if comment is not None and not comment.body.strip():
                set_session(lambda s: replace(s, cursor=comment_row(document, number)))
                update_review(lambda r: delete_comment(r, number))
        return FocusOn(diff_focus), ScrollIntoView(cursor_element, block="nearest")

    def press_row(path: str, index: int, extend: bool) -> InputResponse:
        def pressed(s: Session) -> Session:
            anchor = s.selection or (s.cursor if isinstance(s.cursor, RowAt) else None)
            selection = anchor if extend and anchor is not None and anchor.path == path else None
            return replace(s, cursor=RowAt(path, index), selection=selection, confirming_delete=None)

        set_session(pressed)
        return FocusOn(diff_focus)

    def press_position(position: Position) -> InputResponse:
        match position:
            case CommentAt(number=number):
                return start_editing(number)
            case SummaryAt():
                return start_editing(None)
            case _:
                set_session(lambda s: replace(s, cursor=position, selection=None, confirming_delete=None))
                return FocusOn(diff_focus)

    actions = Actions(
        press_row=press_row,
        press_position=press_position,
        edit_comment=lambda number, edit: update_review(lambda r: edit_comment(r, number, edit)),
        edit_summary=lambda edit: update_review(lambda r: edit_summary(r, edit)),
        finish_editing=finish_editing,
    )

    def comment_on_selection(document: Document, row: RowAt) -> InputResponse:
        first_row, last_row = selection_span(row, session.selection)
        layout = document.layout_of(row)
        started = session.selection or row
        side = primary(layout.rows[started.index]).side
        lines = tuple(line for row in layout.rows[first_row : last_row + 1] for line in row_lines(row))
        anchor, excerpt = anchor_for(layout.file, lines, side, document.diff.base.commit)
        number = open_review.review.next_number
        update_review(lambda r: add_comment(r, anchor, excerpt, open_review.now()))
        return start_editing(number)

    def go(position: Position, block: ScrollAlignment = "nearest") -> InputResponse:
        set_session(lambda s: replace(s, cursor=position, selection=None, confirming_delete=None))
        return ScrollIntoView(cursor_element, block=block)

    def on_key(event: InputEvent, receiver: Receiver) -> InputResponse:
        if not receiver.is_target or document is None:
            return None
        confirming = session.confirming_delete
        if confirming is not None and event != KeyPressed(key="d"):
            set_session(lambda s: replace(s, confirming_delete=None))
        match event:
            case KeyPressed(key=Key.Down | "j"):
                return go(moved(document, cursor, 1))
            case KeyPressed(key=Key.Up | "k"):
                return go(moved(document, cursor, -1))
            case KeyPressed(key=Key.PageDown):
                return go(moved(document, cursor, PAGE_POSITIONS))
            case KeyPressed(key=Key.PageUp):
                return go(moved(document, cursor, -PAGE_POSITIONS))
            case KeyPressed(key=Key.Home):
                return go(document.positions[0])
            case KeyPressed(key=Key.End):
                return go(document.positions[-1])
            case KeyPressed(key=Key.Tab):
                return go(next_stop(document, cursor, 1), block="center")
            case KeyPressed(key=Key.BackTab):
                return go(next_stop(document, cursor, -1), block="center")
            case KeyPressed(key="n"):
                return go(next_comment(document, cursor, 1), block="center")
            case KeyPressed(key="N"):
                return go(next_comment(document, cursor, -1), block="center")
            case KeyPressed(key=Key.ShiftDown | Key.ShiftUp | "J" | "K") if isinstance(cursor, RowAt):
                delta = 1 if event.key in (Key.ShiftDown, "J") else -1
                anchor = session.selection or cursor
                set_session(lambda s: replace(s, cursor=extended(document, cursor, delta), selection=anchor))
                return ScrollIntoView(cursor_element, block="nearest")
            case KeyPressed(key=Key.Escape) if session.selection is not None:
                set_session(lambda s: replace(s, selection=None))
                return ()
            case KeyPressed(key=Key.Enter):
                match cursor:
                    case SummaryAt():
                        return start_editing(None)
                    case CommentAt(number=number):
                        return start_editing(number)
                    case RowAt():
                        return comment_on_selection(document, cursor)
                    case _:
                        return ()
            case KeyPressed(key="r"):
                return start_editing(None)
            case KeyPressed(key="d") if isinstance(cursor, CommentAt):
                number = cursor.number
                if confirming == number:
                    set_session(lambda s: replace(s, cursor=comment_row(document, number), confirming_delete=None))
                    update_review(lambda r: delete_comment(r, number))
                else:
                    set_session(lambda s: replace(s, confirming_delete=number))
                return ScrollIntoView(cursor_element, block="nearest")
            case _:
                return None

    def jump(path: str) -> InputResponse:
        set_session(lambda s: replace(s, cursor=HeaderAt(path), selection=None))
        return FocusOn(diff_focus), ScrollIntoView(cursor_element, block="start")

    def save_preferences(new: Preferences) -> None:
        preferences_file.save(new)
        set_preferences(new)

    def choose_theme(chosen: str) -> InputResponse:
        save_preferences(preferences.model_copy(update={"theme": chosen}))
        set_session(lambda s: replace(s, picking_theme=None))
        return FocusOn(diff_focus)

    def cancel_theme() -> InputResponse:
        set_session(lambda s: replace(s, picking_theme=None))
        return FocusOn(diff_focus)

    def set_query(edit: Callable[[Query], Query]) -> None:
        set_session(
            lambda s: (
                s
                if s.picking_theme is None
                else replace(s, picking_theme=replace(s.picking_theme, query=edit(s.picking_theme.query)))
            )
        )

    def project_reviews() -> tuple[Review, ...]:
        return for_project(open_review.store.reviews(), repository.common_dir, repository.worktree)

    def show_review(review: Review, digest: str | None) -> InputResponse:
        open_review.switch(review, digest)
        set_review(review)
        set_base_mode(review.base_mode if target is not None else BaseMode.HEAD)
        set_session(INITIAL_SESSION)
        return FocusOn(diff_focus)

    def start_new_review() -> InputResponse:
        started = Review.start(
            open_review.new_id(),
            repository.common_dir,
            repository.worktree,
            repository.branch,
            base_mode,
            open_review.now(),
        )
        return show_review(started, None)

    def open_stored_review(review_id: str) -> InputResponse:
        stored = open_review.store.load(review_id)
        return show_review(stored.review, stored.digest)

    def rename_review(review_id: str, name: str) -> None:
        if review_id == open_review.review.id:
            update_review(lambda r: rename(r, name))
        else:
            stored = open_review.store.load(review_id)
            open_review.store.save(rename(stored.review, name), stored.digest)
        listed = project_reviews()
        set_session(lambda s: s if s.managing is None else replace(s, managing=replace(s.managing, reviews=listed)))

    def delete_review(review_id: str) -> None:
        open_review.store.delete(review_id)
        if review_id == open_review.review.id:
            start_new_review()
        listed = project_reviews()
        set_session(lambda s: s if s.managing is None else replace(s, managing=replace(s.managing, reviews=listed)))

    def close_manager() -> InputResponse:
        set_session(lambda s: replace(s, managing=None))
        return FocusOn(diff_focus)

    manager_actions = ManagerActions(
        start_new=start_new_review,
        open=open_stored_review,
        rename=rename_review,
        delete=delete_review,
        close=close_manager,
    )

    def on_global_key(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case KeyPressed(key="q"):
                return Quit()
            case KeyPressed(key="o"):
                handle = FocusHandle()
                listed = project_reviews()
                set_session(lambda s: replace(s, managing=Managing(handle, listed)))
                return FocusOn(handle)
            case KeyPressed(key="s"):
                new_mode = other_mode(mode)
                moved_cursor = in_mode(document, cursor, review.comments, new_mode)
                set_session(lambda s: replace(s, cursor=moved_cursor, selection=None))
                save_preferences(preferences.model_copy(update={"view_mode": new_mode}))
                return ScrollIntoView(cursor_element, block="center")
            case KeyPressed(key="b"):
                if target is None:
                    set_notice("No branch to take a merge base with; pass one with --base")
                    return ()
                new_base = other_base(base_mode)
                set_base_mode(new_base)
                update_review(lambda r: r.model_copy(update={"base_mode": new_base}))
                return ()
            case KeyPressed(key="t"):
                handle = FocusHandle()
                opened = PickingTheme(handle, Query.at(themes(), preferences.theme))
                set_session(lambda s: replace(s, picking_theme=opened))
                return FocusOn(handle)
            case KeyPressed(key="f"):
                return FocusOn(picker_focus)
            case KeyPressed(key="c"):
                copy_notice, copy = copied(review, document)
                set_notice(copy_notice)
                return copy if copy is not None else ()
            case KeyPressed(key=Key.Escape | Key.Tab | Key.BackTab):
                return FocusOn(diff_focus)
            case _:
                return None

    current_path = getattr(cursor, "path", None)
    picker_files = () if diff is None else diff.files
    comment_counts = tuple(
        sum(comment.anchor.path == file.path for comment in review.comments) for file in picker_files
    )

    return Div(
        style=col,
        on_input=on_global_key,
        children=(
            Text(
                content=status_line(review, diff, mode),
                style=text_wrap_none | text_bg("slate", 900),
            ),
            Div(
                style=row | fill(1),
                children=(
                    file_picker(picker_files, comment_counts, current_path, picker_focus, jump),
                    diff_pane(
                        document,
                        review.summary,
                        review.comments,
                        outdated,
                        cursor,
                        session.selection,
                        session.editing,
                        theme,
                        diff_focus,
                        cursor_element,
                        actions,
                        on_key,
                    ),
                ),
            ),
            *(
                (Text(content=f" {notice}", style=text_wrap_none | text_bg("slate", 900) | text_color("amber", 300)),)
                if notice
                else ()
            ),
            Text(content=key_hints(session), style=text_wrap_none | text_bg("slate", 900) | text_color("slate", 400)),
            *(
                (theme_picker(themes(), picking.query, set_query, picking.handle, choose_theme, cancel_theme),)
                if picking is not None
                else ()
            ),
            *(
                (summary_dialog(review.summary, session.editing[1], actions),)
                if session.editing is not None and session.editing[0] is None
                else ()
            ),
            *(
                (
                    review_manager(
                        session.managing.reviews,
                        review.id,
                        str(repository.worktree),
                        session.managing.handle,
                        manager_actions,
                    ),
                )
                if session.managing is not None
                else ()
            ),
        ),
    )
