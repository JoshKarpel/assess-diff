"""
The assess-diff app's root component: the review, the diff it follows, the reader's preferences,
and the cursor, and how keys and presses change them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import assert_never

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
from counterweight.handlers import first
from counterweight.handlers import on_click
from counterweight.handlers import page_down
from counterweight.handlers import page_up
from counterweight.headless import use_text_input
from counterweight.hooks import Editor
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
from assess_diff.navigation import Scope
from assess_diff.navigation import SummaryAt
from assess_diff.navigation import ViewMode
from assess_diff.navigation import after_deleting
from assess_diff.navigation import build_document
from assess_diff.navigation import extended
from assess_diff.navigation import in_view
from assess_diff.navigation import moved
from assess_diff.navigation import next_comment
from assess_diff.navigation import next_stop
from assess_diff.navigation import placements
from assess_diff.navigation import primary
from assess_diff.navigation import recovered
from assess_diff.navigation import recovered_selection
from assess_diff.navigation import selected_lines
from assess_diff.navigation import selection_span
from assess_diff.preferences import Preferences
from assess_diff.preferences import PreferencesFile
from assess_diff.preferences import themes
from assess_diff.review import FileAnchor
from assess_diff.review import Review
from assess_diff.review import add_comment
from assess_diff.review import anchor_for
from assess_diff.review import delete_comment
from assess_diff.review import edit_comment
from assess_diff.review import edit_summary
from assess_diff.review import is_outdated
from assess_diff.review import rename
from assess_diff.storage import Listing
from assess_diff.storage import OpenReview
from assess_diff.storage import Unreadable
from assess_diff.storage import for_project
from assess_diff.ui.comment_jump import comment_jump
from assess_diff.ui.comment_jump import wanted
from assess_diff.ui.diff_view import Actions
from assess_diff.ui.diff_view import diff_pane
from assess_diff.ui.diff_view import summary_dialog
from assess_diff.ui.diff_view import use_stable_actions
from assess_diff.ui.file_picker import file_picker
from assess_diff.ui.file_picker import marked
from assess_diff.ui.pills import ON_SCREEN
from assess_diff.ui.pills import key_pills
from assess_diff.ui.pills import pills
from assess_diff.ui.review_manager import ManagerActions
from assess_diff.ui.review_manager import review_manager
from assess_diff.ui.theme_picker import Query
from assess_diff.ui.theme_picker import highlighted_theme
from assess_diff.ui.theme_picker import theme_picker

# How long the watcher waits for a burst of changes to settle before the diff is recomputed.
DEBOUNCE_MILLISECONDS = 200


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
    naming: Naming | None
    jumping: Jumping | None


@dataclass(frozen=True, slots=True)
class Jumping:
    """The dialog to go to a comment by its number is open: its field's focus handle, and the digits typed so far."""

    handle: FocusHandle
    number: str


@dataclass(frozen=True, slots=True)
class Naming:
    """The open review's name is being edited in the status line: its field's focus handle, and what's typed so far."""

    handle: FocusHandle
    name: str


@dataclass(frozen=True, slots=True)
class PickingTheme:
    """The theme picker is open: its focus handle, and what's typed and highlighted in it."""

    handle: FocusHandle
    query: Query


@dataclass(frozen=True, slots=True)
class Managing:
    """
    The review manager is open: its focus handle, and the project's reviews and the store's unreadable files
    it lists, read when it opened or last changed.
    """

    handle: FocusHandle
    listing: Listing


INITIAL_SESSION = Session(
    cursor=SummaryAt(),
    selection=None,
    editing=None,
    confirming_delete=None,
    picking_theme=None,
    managing=None,
    naming=None,
    jumping=None,
)


def other_mode(mode: ViewMode) -> ViewMode:
    return ViewMode.SPLIT if mode is ViewMode.UNIFIED else ViewMode.UNIFIED


def other_scope(scope: Scope) -> Scope:
    return Scope.WHOLE if scope is Scope.CONTEXT else Scope.CONTEXT


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


def unreadable_notice(unreadable: tuple[Unreadable, ...]) -> str | None:
    """What the app says as it starts about review files it can't read, which the review manager lists."""
    if not unreadable:
        return None
    return f"{plural(len(unreadable), 'review file')} can't be read; o lists {'it' if len(unreadable) == 1 else 'them'}"


NAME_STYLE = text_bold | text_color("amber", 300)


@component
def name_field(
    naming: Naming,
    set_name: Editor[str],
    rename: Callable[[str], InputResponse],
    cancel: Callable[[], InputResponse],
) -> Text:
    """The open review's name in the status line as a field: Enter renames the review to what's typed, Escape doesn't."""
    field = use_text_input(naming.name, set_name, handle=naming.handle)

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case KeyPressed(key=Key.Enter):
                return rename(naming.name)
            case KeyPressed(key=Key.Escape):
                return cancel()
            case _:
                return field.on_input(event, receiver)

    cursor = (text_color("slate", 900) | text_bg("amber", 300)).text_style
    return Text(
        content=(
            Chunk(content=" "),
            Chunk(content=field.before_cursor),
            Chunk(content=field.at_cursor, style=cursor),
            Chunk(content=field.after_cursor),
            Chunk(content=" "),
        ),
        style=NAME_STYLE | text_wrap_none | text_bg("slate", 700),
        on_input=first(on_input, field.on_press),
    )


def status_line(review: Review, diff: Diff | None, mode: ViewMode) -> tuple[tuple[Chunk, ...], ...]:
    """Everything the status line says after the review's name, in the groups it wraps between."""
    branch = review.branch or "detached HEAD"
    comments = len(review.comments)
    if diff is None:
        against = "reading the diff…"
    elif diff.base.mode is BaseMode.HEAD:
        against = f"against HEAD {diff.base.short}"
    else:
        against = f"against {diff.base.label} (merge base {diff.base.short})"
    files = (
        ()
        if diff is None
        else (
            (
                Chunk(content=plural(len(diff.files), "file"), style=text_color("slate", 400)),
                Chunk(content=f" +{sum(file.additions for file in diff.files)}", style=text_color("green", 400)),
                Chunk(content=f" -{sum(file.deletions for file in diff.files)}", style=text_color("red", 400)),
            ),
        )
    )
    return (
        (Chunk(content=branch, style=text_bold),),
        (Chunk(content=against, style=text_color("slate", 300)),),
        *files,
        (Chunk(content=plural(comments, "comment"), style=text_color("slate", 400)),),
        (Chunk(content=str(mode), style=text_color("slate", 400)),),
    )


def key_hints(session: Session, are_keys_shown: bool) -> tuple[str, ...]:
    """
    The keys of what has focus, each with what it does; the diff's are folded away unless `are_keys_shown`,
    but a dialog's or a confirmation's always show.
    """
    if session.naming is not None:
        return ("enter rename", "esc keep the old name")
    if session.jumping is not None:
        return ("type a number", "enter go", "esc close")
    if session.managing is not None:
        return ("↑↓ choose", "enter open", "e rename", "dd delete", "esc close")
    if session.picking_theme is not None:
        return ("type to filter", "↑↓ preview", "enter keep", "esc revert")
    if session.editing is not None:
        return ("esc done",)
    if session.confirming_delete is not None:
        return (f"press d again to delete #{session.confirming_delete}", "anything else to keep it")
    if not are_keys_shown:
        return ()
    return (
        "↑↓←→ move",
        "⇧↑↓ select",
        "tab hunk/comment",
        "n/N comment",
        "# go to #",
        "enter comment",
        "dd delete",
        "r summary",
        "e rename",
        "s split",
        "z fold unchanged",
        "b base",
        "t theme",
        "f files",
        "F fold files",
        "o reviews",
        "c copy",
        "⇧? fold keys",
        "q quit",
    )


@component
def assess_app(
    repository: Repository,
    target: str | None,
    open_review: OpenReview,
    preferences_file: PreferencesFile,
    initial_preferences: Preferences,
    initial_diff: Diff | None,
    unreadable: tuple[Unreadable, ...],
) -> Div:
    """
    The whole app. `target` is the branch the merge base is taken with,
    or `None` when there isn't one, which leaves the base at `HEAD`.
    `initial_diff` is drawn until the app computes the diff itself, as it starts watching;
    the CLI computes it before the app starts, so the first frame draws it,
    and it's `None` only when that failed, which draws a line saying it's being read.
    `unreadable` are the store's files the CLI couldn't read as it started, which the first notice mentions.
    """
    review, set_review = use_state(open_review.review)
    preferences, set_preferences = use_state(initial_preferences)
    no_notice: str | None = None
    base_mode, set_base_mode = use_state(review.base_mode if target is not None else BaseMode.HEAD)
    diff, set_diff = use_state(initial_diff)
    session, set_session = use_state(INITIAL_SESSION)
    # Apart from the session, so they stay as they are when another review opens.
    are_files_shown, set_files_shown = use_state(True)
    are_keys_shown, set_keys_shown = use_state(True)
    notice, set_notice = use_state(unreadable_notice(unreadable))
    # Apart from `notice`, so the refresh that recovers from a git failure can clear it without clearing any other.
    git_error, set_git_error = use_state(no_notice)
    diff_focus, _ = use_state(FocusHandle)
    picker_focus, _ = use_state(FocusHandle)
    cursor_element, _ = use_state(ElementHandle)

    async def follow_diff() -> None:
        async def refresh() -> None:
            try:
                base = await resolve_base(repository, base_mode, target or "HEAD")
                set_diff(await compute_diff(repository, base))
            except GitError as error:
                set_git_error(str(error))
            else:
                set_git_error(None)

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
    scope = preferences.scope
    picking = session.picking_theme
    theme = (highlighted_theme(themes(), picking.query) if picking is not None else None) or preferences.theme
    document = use_memo(
        lambda: None if diff is None else build_document(diff, review.comments, mode, scope),
        (diff, placements(review.comments), mode, scope),
    )
    outdated = use_memo(
        lambda: frozenset(
            comment.number for comment in review.comments if diff is not None and is_outdated(comment, diff)
        ),
        (diff, review.comments),
    )
    cursor = SummaryAt() if document is None else recovered(document, session.cursor)
    selection = None if document is None else recovered_selection(document, cursor, session.selection)

    def update_review(edit: Callable[[Review], Review]) -> Review:
        if (saved_notice := open_review.update(edit)) is not None:
            set_notice(saved_notice)
        return set_review(open_review.review)

    def edit_comment_body(number: int, edit: Callable[[str], str]) -> str:
        """Edit comment `number`'s body, returning it as it settled, or nothing typed once the comment's gone."""
        comment = update_review(lambda r: edit_comment(r, number, edit)).comment(number)
        return "" if comment is None else comment.body

    def start_editing(number: int | None) -> InputResponse:
        """Edit comment `number` where it's drawn, or with `None`, the summary in its dialog, leaving the cursor be."""
        # An editor that's already open keeps its handle: counterweight refuses a mounted component a new one.
        editing = session.editing
        handle = editing[1] if editing is not None and editing[0] == number else FocusHandle()
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
                set_session(lambda s: replace(s, cursor=after_deleting(document, number, review.comments)))
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

    actions = use_stable_actions(
        Actions(
            press_row=press_row,
            press_position=press_position,
            edit_comment=edit_comment_body,
            edit_summary=lambda edit: update_review(lambda r: edit_summary(r, edit)).summary,
            finish_editing=finish_editing,
        )
    )

    def comment_on_selection(document: Document, row: RowAt) -> InputResponse:
        first_row, last_row = selection_span(row, selection)
        layout = document.layout_of(row)
        started = selection or row
        side = primary(layout.rows[started.index]).side
        lines = selected_lines(layout, first_row, last_row)
        anchor, excerpt = anchor_for(layout.file, lines, side, document.diff.base.commit)
        number = open_review.review.next_number
        update_review(lambda r: add_comment(r, anchor, excerpt, open_review.now()))
        return start_editing(number)

    def comment_on_file(path: str) -> InputResponse:
        number = open_review.review.next_number
        update_review(lambda r: add_comment(r, FileAnchor(path=path, side=None), (), open_review.now()))
        return start_editing(number)

    def go(position: Position, block: ScrollAlignment = "nearest") -> ScrollIntoView:
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
            # As a text area pages: the view scrolls by a page, and the cursor moves as many positions as it scrolls,
            # which keeps it in place on screen across diff rows, and short of it across taller comments.
            case KeyPressed(key=Key.PageDown) if (scroll := receiver.scroll) is not None:
                page = page_down(receiver.subtree, scroll)
                return page, go(moved(document, cursor, page.y))
            case KeyPressed(key=Key.PageUp) if (scroll := receiver.scroll) is not None:
                page = page_up(receiver.subtree, scroll)
                return page, go(moved(document, cursor, page.y))
            case KeyPressed(key=Key.Home):
                return go(document.positions[0])
            case KeyPressed(key=Key.End):
                return go(document.positions[-1])
            case KeyPressed(key=Key.Tab):
                return go(next_stop(document, cursor, forward=True), block="center")
            case KeyPressed(key=Key.BackTab):
                return go(next_stop(document, cursor, forward=False), block="center")
            case KeyPressed(key="n"):
                return go(next_comment(document, cursor, forward=True), block="center")
            case KeyPressed(key="N"):
                return go(next_comment(document, cursor, forward=False), block="center")
            case KeyPressed(key=Key.ShiftDown | Key.ShiftUp | "J" | "K") if isinstance(cursor, RowAt):
                delta = 1 if event.key in (Key.ShiftDown, "J") else -1
                anchor = selection or cursor
                set_session(lambda s: replace(s, cursor=extended(document, cursor, delta), selection=anchor))
                return ScrollIntoView(cursor_element, block="nearest")
            case KeyPressed(key=Key.Escape) if selection is not None:
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
                    case HeaderAt(path=path):
                        return comment_on_file(path)
                    case _ as unreachable:
                        assert_never(unreachable)
            case KeyPressed(key="r"):
                return start_editing(None)
            case KeyPressed(key="d") if isinstance(cursor, CommentAt):
                number = cursor.number
                if confirming == number:
                    set_session(
                        lambda s: replace(
                            s, cursor=after_deleting(document, number, review.comments), confirming_delete=None
                        )
                    )
                    update_review(lambda r: delete_comment(r, number))
                else:
                    set_session(lambda s: replace(s, confirming_delete=number))
                return ScrollIntoView(cursor_element, block="nearest")
            case _:
                return None

    def jump(target: HeaderAt | SummaryAt) -> InputResponse:
        set_session(lambda s: replace(s, cursor=target, selection=None, confirming_delete=None))
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

    def set_query(edit: Callable[[Query], Query]) -> Query:
        settled = set_session(
            lambda s: (
                s
                if s.picking_theme is None
                else replace(s, picking_theme=replace(s.picking_theme, query=edit(s.picking_theme.query)))
            )
        )
        # A closed picker has nothing typed in it.
        return Query(text="", highlighted=0) if settled.picking_theme is None else settled.picking_theme.query

    def project_listing() -> Listing:
        """The store as the review manager lists it: this project's reviews, and every file that can't be read."""
        listing = open_review.store.listed()
        return replace(listing, reviews=for_project(listing.reviews, repository.common_dir, repository.worktree))

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
        listed = project_listing()
        set_session(lambda s: s if s.managing is None else replace(s, managing=replace(s.managing, listing=listed)))

    def delete_review(review_id: str) -> None:
        open_review.store.delete(review_id)
        if review_id == open_review.review.id:
            # A new review takes its place behind the dialog, which stays open and focused to go on managing.
            managing = session.managing
            start_new_review()
            set_session(lambda s: replace(s, managing=managing))
        listed = project_listing()
        set_session(lambda s: s if s.managing is None else replace(s, managing=replace(s.managing, listing=listed)))

    def close_manager() -> InputResponse:
        set_session(lambda s: replace(s, managing=None))
        return FocusOn(diff_focus)

    def start_naming() -> InputResponse:
        """Edit the open review's name in the status line, carrying on with what's typed if it's already open."""
        naming = session.naming or Naming(FocusHandle(), review.name)
        set_session(lambda s: replace(s, naming=naming))
        return FocusOn(naming.handle)

    def set_name(edit: Callable[[str], str]) -> str:
        settled = set_session(
            lambda s: s if s.naming is None else replace(s, naming=replace(s.naming, name=edit(s.naming.name)))
        )
        # A closed field has nothing typed in it.
        return "" if settled.naming is None else settled.naming.name

    def finish_naming(typed: str) -> InputResponse:
        # As the review manager renames: surrounding spaces go, but a name of only spaces stays rather than vanishing.
        name = typed.strip() or typed
        update_review(lambda r: rename(r, name))
        return cancel_naming()

    def cancel_naming() -> InputResponse:
        set_session(lambda s: replace(s, naming=None))
        return FocusOn(diff_focus)

    def start_jumping() -> InputResponse:
        """Open the dialog to go to a comment by its number, carrying on with what's typed if it's already open."""
        jumping = session.jumping or Jumping(FocusHandle(), "")
        set_session(lambda s: replace(s, jumping=jumping))
        return FocusOn(jumping.handle)

    def set_jump_number(edit: Callable[[str], str]) -> str:
        settled = set_session(
            lambda s: s if s.jumping is None else replace(s, jumping=replace(s.jumping, number=edit(s.jumping.number)))
        )
        # A closed dialog has nothing typed in it.
        return "" if settled.jumping is None else settled.jumping.number

    def go_to_comment(number: int) -> InputResponse:
        set_session(
            lambda s: replace(s, cursor=CommentAt(number), selection=None, confirming_delete=None, jumping=None)
        )
        return FocusOn(diff_focus), ScrollIntoView(cursor_element, block="center")

    def cancel_jumping() -> InputResponse:
        set_session(lambda s: replace(s, jumping=None))
        return FocusOn(diff_focus)

    def return_to_diff() -> InputResponse:
        """Focus the diff, closing any dialog and finishing any edit on the way, so nothing is left open unfocused."""
        set_session(lambda s: replace(s, picking_theme=None, managing=None, naming=None, jumping=None))
        if session.editing is not None:
            return finish_editing()
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
                handle = session.managing.handle if session.managing is not None else FocusHandle()
                listed = project_listing()
                set_session(lambda s: replace(s, managing=Managing(handle, listed)))
                return FocusOn(handle)
            case KeyPressed(key="s"):
                new_mode = other_mode(mode)
                moved_cursor = in_view(document, cursor, review.comments, new_mode, scope)
                set_session(lambda s: replace(s, cursor=moved_cursor, selection=None))
                save_preferences(preferences.model_copy(update={"view_mode": new_mode}))
                return ScrollIntoView(cursor_element, block="center")
            case KeyPressed(key="z"):
                new_scope = other_scope(scope)
                moved_cursor = in_view(document, cursor, review.comments, mode, new_scope)
                set_session(lambda s: replace(s, cursor=moved_cursor, selection=None))
                save_preferences(preferences.model_copy(update={"scope": new_scope}))
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
                handle = session.picking_theme.handle if session.picking_theme is not None else FocusHandle()
                opened = PickingTheme(handle, Query.at(themes(), preferences.theme))
                set_session(lambda s: replace(s, picking_theme=opened))
                return FocusOn(handle)
            case KeyPressed(key="e"):
                return start_naming()
            case KeyPressed(key="#"):
                return start_jumping()
            case KeyPressed(key="f"):
                set_files_shown(True)
                return FocusOn(picker_focus)
            case KeyPressed(key="F"):
                set_files_shown(not are_files_shown)
                # Folding the files away unmounts the picker, which may have focus.
                return FocusOn(diff_focus) if are_files_shown else ()
            case KeyPressed(key="?"):
                set_keys_shown(not are_keys_shown)
                return ()
            case KeyPressed(key="c"):
                copy_notice, copy = copied(review, document)
                set_notice(copy_notice)
                return copy if copy is not None else ()
            case KeyPressed(key=Key.Escape | Key.Tab | Key.BackTab):
                return return_to_diff()
            case _:
                return None

    picker_files = () if diff is None else diff.files
    comment_counts = tuple(
        sum(comment.anchor.path == file.path for comment in review.comments) for file in picker_files
    )
    hints = key_hints(session, are_keys_shown)

    return Div(
        style=col,
        on_input=on_global_key,
        children=(
            Div(
                style=row | flex_wrap | gap_width(1),
                children=(
                    Text(content=" assess-diff ", style=text_bold | text_wrap_none | text_bg("indigo", 700)),
                    name_field(session.naming, set_name, finish_naming, cancel_naming)
                    if session.naming is not None
                    else Text(
                        content=f" {review.name} ",
                        style=NAME_STYLE | text_wrap_none | ON_SCREEN,
                        on_input=on_click(start_naming),
                    ),
                    # Its own row, under the name, when it doesn't fit beside it, wrapping between pills there.
                    Text(content=pills(status_line(review, diff, mode)), style=text_wrap_stable | grow(1)),
                ),
            ),
            Div(
                style=row | fill(1),
                children=(
                    *(
                        (file_picker(picker_files, comment_counts, marked(cursor, review), picker_focus, jump),)
                        if are_files_shown
                        else ()
                    ),
                    diff_pane(
                        document,
                        review.summary,
                        review.comments,
                        outdated,
                        cursor,
                        selection,
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
                Text(
                    content=line,
                    style=text_wrap_stable
                    | text_justify_center
                    | pad_x(1)
                    | text_bg("slate", 900)
                    | text_color("amber", 300),
                )
                for line in (git_error, notice)
                if line
            ),
            *(
                (
                    Text(
                        content=key_pills(hints),
                        style=text_wrap_stable,
                    ),
                )
                if hints
                else ()
            ),
            *(
                (theme_picker(themes(), picking.query, set_query, picking.handle, choose_theme, cancel_theme),)
                if picking is not None
                else ()
            ),
            *(
                (
                    comment_jump(
                        session.jumping.number,
                        set_jump_number,
                        session.jumping.handle,
                        wanted(session.jumping.number, review),
                        go_to_comment,
                        cancel_jumping,
                    ),
                )
                if session.jumping is not None
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
                        session.managing.listing.reviews,
                        session.managing.listing.unreadable,
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
