"""Runs the whole app headless on a scratch repository, for end-to-end tests of what keys and presses do."""

from __future__ import annotations

import sys
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from counterweight import app
from counterweight.asciicast import Recording
from counterweight.autopilot import AutopilotStep
from counterweight.autopilot import WaitFor
from counterweight.controls import Quit
from counterweight.controls import Screenshot
from counterweight.events import KeyPressed
from counterweight.events import MouseDown
from counterweight.events import MouseUp
from counterweight.geometry import Position
from counterweight.paint import Frame

from assess_diff.diff import Diff
from assess_diff.git import find_repository
from assess_diff.preferences import PreferencesFile
from assess_diff.review import FOLLOW_HEAD
from assess_diff.review import BaseChoice
from assess_diff.review import Review
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewStore
from assess_diff.ui.app import assess_app
from tests.conftest import Scratch

REVIEW_ID = "0199bbbb-review"
CLOCK = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
WIDTH, HEIGHT = 110, 32


def keys(*names: str) -> list[KeyPressed]:
    return [KeyPressed(key=name) for name in names]


def typed(text: str) -> list[KeyPressed]:
    return [KeyPressed(key=character) for character in text]


def click(x: int, y: int, *, shift: bool = False) -> list[MouseDown | MouseUp]:
    position = Position(x, y)
    return [MouseDown(absolute=position, button=1, is_shift_held=shift), MouseUp(absolute=position, button=1)]


def screen_text(frame: Frame) -> str:
    """A screen's text as it reads: no-break spaces, which keep words together in the header and footer, as spaces."""
    return frame.text(ansi=False).replace("\N{NO-BREAK SPACE}", " ")


def showing(text: str) -> WaitFor:
    return WaitFor(lambda frame: text in screen_text(frame), timeout=timedelta(seconds=10))


def where(frame: str, text: str) -> Position:
    """Where `text` first appears on a screen's text, to press it."""
    for y, line in enumerate(frame.splitlines()):
        if (x := line.find(text)) >= 0:
            return Position(x, y)
    raise AssertionError(f"{text!r} isn't on the screen:\n{frame}")


@dataclass
class Harness:
    scratch: Scratch
    store: ReviewStore
    preferences: PreferencesFile
    frames: list[str] = field(default_factory=list)
    colored: list[str] = field(default_factory=list)

    def shot(self) -> Screenshot:
        def keep(frame: Frame) -> None:
            self.frames.append(screen_text(frame))
            self.colored.append(frame.text(ansi=True))

        return Screenshot(handler=keep)

    def review(self) -> Review:
        return self.store.load(REVIEW_ID).review

    async def run(
        self,
        steps: Sequence[AutopilotStep],
        *,
        target: str | None = "main",
        loaded: str = "against ",
        quit_with: AutopilotStep = Quit(),
        open_review: Callable[[OpenReview], OpenReview] = lambda opened: opened,
        initial_diff: Diff | None = None,
        base: BaseChoice = FOLLOW_HEAD,
        recording: Recording | None = None,
    ) -> OpenReview:
        """
        Runs the app, given `initial_diff` to draw until it computes its own, until the diff shows `loaded`,
        on a review against `base` unless one is stored already,
        then takes `steps`, then quits; returns the open review.
        `recording` records every frame the app writes, which a screenshot, taken only once the app settles, can't.
        """
        repository = await find_repository(self.scratch.root)
        stored = self.store.path(REVIEW_ID)
        if stored.exists():
            loaded_review = self.store.load(REVIEW_ID)
            opened = OpenReview(self.store, loaded_review.review, loaded_review.digest, lambda: "forked", lambda: CLOCK)
        else:
            started = Review.start(
                REVIEW_ID, repository.common_dir, repository.worktree, repository.branch, base, CLOCK
            )
            opened = OpenReview(self.store, started, None, lambda: "forked", lambda: CLOCK)
        opened = open_review(opened)
        await app(
            assess_app(
                repository,
                target,
                opened,
                self.preferences,
                self.preferences.load(),
                initial_diff,
                self.store.listed().unreadable,
            ),
            # counterweight binds these defaults to the streams in place when it's first imported,
            # which mutmut has closed by the time it runs the tests a second time in the same process.
            output_stream=sys.stdout,
            stdout=sys.stdout,
            stderr=sys.stderr,
            headless=True,
            dimensions=(WIDTH, HEIGHT),
            autopilot=[showing(loaded), *steps, quit_with],
            recording=recording,
        )
        return opened
