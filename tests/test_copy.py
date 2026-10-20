from __future__ import annotations

import io
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from counterweight.app import app
from counterweight.autopilot import WaitFor
from counterweight.controls import Quit
from counterweight.events import KeyPressed
from counterweight.output import clipboard_instructions

from assess_diff.diff import BaseMode
from assess_diff.export import to_markdown
from assess_diff.git import compute_diff
from assess_diff.git import find_repository
from assess_diff.git import resolve_base
from assess_diff.preferences import Preferences
from assess_diff.preferences import PreferencesFile
from assess_diff.review import Review
from assess_diff.review import edit_summary
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewStore
from assess_diff.ui.app import assess_app
from tests.conftest import Scratch
from tests.conftest import Terminal

CLOCK = datetime(2026, 7, 7, 7, 7, tzinfo=UTC)


async def test_c_copies_the_review_as_markdown(scratch: Scratch, terminal: Terminal, tmp_path: Path) -> None:
    scratch.write("app.py", "one = 1\n")
    scratch.commit()
    scratch.write("app.py", "one = 2\n")
    repository = await find_repository(scratch.root)
    review = Review.start("copied", repository.common_dir, repository.worktree, "main", BaseMode.HEAD, CLOCK)
    open_review = OpenReview(ReviewStore(tmp_path / "reviews"), review, None, lambda: "new", lambda: CLOCK)
    open_review.update(lambda r: edit_summary(r, lambda _: "Copy me."))
    output = io.StringIO()

    await app(
        assess_app(
            repository, "main", open_review, PreferencesFile(tmp_path / "preferences.json"), Preferences.defaults()
        ),
        output_stream=output,
        input_stream=terminal.stream,
        dimensions=(80, 20),
        autopilot=[
            WaitFor(lambda frame: "against HEAD" in frame.text(ansi=False), timeout=timedelta(seconds=10)),
            KeyPressed(key="c"),
            Quit(),
        ],
    )

    diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))
    assert clipboard_instructions(to_markdown(open_review.review, diff)) in output.getvalue()
