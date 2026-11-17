"""
The dialog `/` opens to find text in the diff, such as a name: it lists the lines holding what's typed,
each with its place and the text around the match, and the diff behind it previews the highlighted one.
Docked at the bottom, as the summary dialog is, since the preview centers the line's row, which should stay in view.
"""

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
from counterweight.events import MouseScrolledDown
from counterweight.events import MouseScrolledUp
from counterweight.events import Receiver
from counterweight.events import response_controls
from counterweight.focus import ElementHandle
from counterweight.focus import FocusHandle
from counterweight.handlers import on_click
from counterweight.headless import use_text_input
from counterweight.hooks import Editor
from counterweight.hooks import use_rects
from counterweight.hooks import use_ref
from counterweight.keys import Key
from counterweight.styles import Style
from counterweight.styles.utilities import *

from assess_diff.search import Match
from assess_diff.ui.diff_view import docked
from assess_diff.ui.file_picker import run_on_mount
from assess_diff.ui.rows import MARKER_COLORS
from assess_diff.ui.rows import MARKERS
from assess_diff.ui.rows import cell_width
from assess_diff.ui.rows import chunks_width
from assess_diff.ui.rows import displayed
from assess_diff.ui.rows import fitted
from assess_diff.ui.rows import scrolled

MOST_SHOWN = 10
"""
The most matches listed at once, around the highlighted one.
Only these are drawn, rather than every match in a scroll, since a short query can match most of a large diff.
"""

COVERED_BELOW_LIST = 8
"""
The rows of the screen below the middle that the dialog takes beyond its list: its border, title, field and tally,
the gap it's docked above the bottom by, the footer, and a row of the diff to spare under the previewed one.
"""

MATCH_STYLE = text_bold | text_color("amber", 300)
PLACE_STYLE = text_color("slate", 400)


def place(match: Match) -> str:
    """Where a match is: its file and its line's number on the line's own side, which its marker tells."""
    return f"{match.at.path}:{match.line.number}"


def excerpt(match: Match, width: int) -> tuple[Chunk, ...]:
    """
    The matched line's text, without its indentation, cut to exactly `width` cells around the match,
    with a third of the width before it where the line runs on that far, and `…` where the text is cut.
    """
    text = match.line.text
    pieces = (
        Chunk(content=displayed(text[: match.start].lstrip())),
        Chunk(content=displayed(text[match.start : match.stop]), style=MATCH_STYLE),
        Chunk(content=displayed(text[match.stop :])),
    )
    offset = max(0, min(cell_width(pieces[0].content) - width // 3, chunks_width(pieces) - width))
    return fitted(scrolled(pieces, offset, Style()), width, Style())


def shown_for(screen_height: int) -> int:
    """
    How many matches the dialog lists on a screen `screen_height` rows high:
    as many as keep it below the middle of the screen, where the diff centers the previewed row, and at least one.
    """
    return max(1, min(MOST_SHOWN, screen_height // 2 - COVERED_BELOW_LIST))


def listed_from(highlighted: int, count: int, shown: int) -> int:
    """The first of the `shown` matches listed: the highlighted one in the middle of them, but for near either end."""
    return max(0, min(highlighted - shown // 2, count - shown))


def tally(query: str, found: tuple[Match, ...], highlighted: int | None) -> str:
    """The dialog's last line: which match is highlighted of how many, or why nothing is listed."""
    if not query:
        return "Type text to find, such as a name"
    if highlighted is None:
        return f"No line holds “{query}”"
    return f"{highlighted + 1} of {len(found)} {'line' if len(found) == 1 else 'lines'}"


@component
def text_search(
    query: str,
    set_query: Editor[str],
    handle: FocusHandle,
    found: tuple[Match, ...],
    highlighted: int | None,
    shown: int,
    preview: ElementHandle,
    move: Callable[[int], None],
    go: Callable[[int], InputResponse],
    cancel: Callable[[], InputResponse],
    dismiss: Callable[[], None],
) -> Div:
    """
    The dialog over the diff, listing `shown` of the matches, controlled by the app, which keeps what's typed
    so `/` returns to it, finds the matches, and chooses which is `highlighted`, putting the diff's cursor on it.
    ↑↓ and the wheel over the dialog `move` the highlight, and each move or edit brings `preview`, the cursor's element, into view.
    Enter or a click `go`es to a match, and Escape or Tab `cancel`s, leaving the cursor where it was.
    It's `dismiss`ed once its field loses focus, however it loses it,
    such as to a click on the diff, so it's never left open over a cursor it isn't previewing.
    """
    field = use_text_input(query, set_query, handle=handle)
    has_been_focused = use_ref(False)
    width = use_rects().content.width

    # As the file picker's filter does: the field mounts unfocused and takes focus a render later.
    if field.focus.is_focused:
        has_been_focused.current = True
    is_blurred = has_been_focused.current and not field.focus.is_focused

    in_view = ScrollIntoView(preview, block="center")

    def on_input(event: InputEvent, receiver: Receiver) -> InputResponse:
        match event:
            case KeyPressed(key=Key.Down) | MouseScrolledDown():
                move(1)
            case KeyPressed(key=Key.Up) | MouseScrolledUp():
                move(-1)
            case KeyPressed(key=Key.Enter) if highlighted is not None:
                return go(highlighted)
            # Tab too, which would otherwise leave the diff scrolled to a match the cursor is no longer on.
            case KeyPressed(key=Key.Escape | Key.Tab | Key.BackTab):
                return cancel()
            case _:
                if (edited := response_controls(field.on_input(event, receiver))) is None:
                    return None
                return (*edited, in_view)
        return in_view

    start = 0 if highlighted is None else listed_from(highlighted, len(found), shown)
    listed = tuple(enumerate(found[start : start + shown], start=start))
    places = {index: place(match) for index, match in listed}
    place_width = max((len(text) for text in places.values()), default=0)
    # The place, a space, the marker and a space before the text, inside the entry's padding.
    text_width = max(0, width - 2 - place_width - 3)

    cursor = (text_color("slate", 900) | text_bg("slate", 100)).text_style
    return Div(
        style=docked | col | border | border_lightrounded | border_color("amber", 400) | text_bg("slate", 900),
        on_input=on_input,
        children=(
            Text(content="Find text", style=text_bold | pad_x(1)),
            Text(
                content=(
                    Chunk(content="/ ", style=text_color("amber", 300)),
                    Chunk(content=field.before_cursor),
                    Chunk(content=field.at_cursor, style=cursor),
                    Chunk(content=field.after_cursor),
                ),
                style=pad_x(1) | text_wrap_none,
                on_input=field.on_press,
            ),
            Div(
                style=col,
                children=tuple(
                    Text(
                        content=(
                            Chunk(content=places[index].ljust(place_width), style=PLACE_STYLE),
                            Chunk(content=f" {MARKERS[match.line.kind]} ", style=MARKER_COLORS[match.line.kind]),
                            *excerpt(match, text_width),
                        ),
                        style=pad_x(1) | text_wrap_none | (text_bg("indigo", 800) if index == highlighted else default),
                        on_input=on_click(partial(go, index)),
                    )
                    for index, match in listed
                ),
            ),
            Text(content=tally(query, found, highlighted), style=pad_x(1) | text_color("slate", 500)),
            *((run_on_mount(dismiss),) if is_blurred else ()),
        ),
    )
