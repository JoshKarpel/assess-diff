from __future__ import annotations

import io
import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from counterweight import app
from counterweight.autopilot import WaitFor
from counterweight.controls import Quit
from counterweight.events import KeyPressed

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
from tests.harness import screen_text

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
    copies: list[str] = []

    await app(
        assess_app(
            repository,
            "main",
            open_review,
            PreferencesFile(tmp_path / "preferences.json"),
            Preferences.defaults(),
            None,
            (),
        ),
        output_stream=output,
        # As the harness passes them: counterweight's defaults are the streams in place at its import.
        stdout=sys.stdout,
        stderr=sys.stderr,
        clipboard_handler=copies.append,
        input_stream=terminal.stream,
        dimensions=(80, 20),
        autopilot=[
            WaitFor(lambda frame: "against HEAD" in screen_text(frame), timeout=timedelta(seconds=10)),
            KeyPressed(key="c"),
            Quit(),
        ],
    )

    diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))
    assert copies == [to_markdown(open_review.review, diff)]
