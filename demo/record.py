"""
The README's demo: the app reviewing a small change, recorded as an asciicast and drawn as a GIF with agg.

The recording is the same on every run.
The repository is built with fixed commit dates and no git configuration but its own, so its hashes don't change,
the review's clock and id are fixed,
and the app runs on a frozen clock that only the walkthrough's pauses move.
The diff is computed before the app starts, as the CLI does,
so the walkthrough never waits on git, which would bring real time into the recording.

The asciicast is committed beside the GIF, and the GIF is drawn again only when the asciicast changes.
agg draws with the fonts each machine has, so the GIF's bytes differ between machines while the asciicast's don't:
the pre-commit hook that runs this can check the demo is current anywhere, and draws only where something changed.
`just demo` runs it too.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import tempfile
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from counterweight import app
from counterweight.asciicast import Recording
from counterweight.autopilot import AutopilotStep
from counterweight.autopilot import paced
from counterweight.autopilot import typed
from counterweight.clock import FrozenClock
from counterweight.controls import AdvanceClock
from counterweight.controls import Marker
from counterweight.controls import Quit
from counterweight.events import KeyPressed
from counterweight.keys import Key

from assess_diff.diff import BaseMode
from assess_diff.git import compute_diff
from assess_diff.git import find_repository
from assess_diff.git import resolve_base
from assess_diff.preferences import PreferencesFile
from assess_diff.review import Review
from assess_diff.review import rename
from assess_diff.storage import OpenReview
from assess_diff.storage import ReviewStore
from assess_diff.ui.app import assess_app
from tests.conftest import Scratch
from tests.conftest import init_scratch

CLOCK = datetime(2026, 10, 20, 14, 3, tzinfo=UTC)
DIMENSIONS = (120, 34)
CAST = Path(__file__).parent / "demo.cast"
GIF = Path(__file__).parent / "demo.gif"

# git reads only the repository's own configuration, and commits at a fixed time, so every hash is the same.
GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_DATE": "2026-10-20T14:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-10-20T14:00:00+00:00",
}

FETCH = '''"""Fetch forecasts from the weather service."""

import json
{imports}from urllib.request import urlopen

{cache_import}from config import Settings


def forecast_url(settings: Settings, city: str) -> str:
    return f"{{settings.endpoint}}/forecast?city={{city}}&units={{settings.units}}"


{fetch}

def summarize(forecast: dict) -> str:
    today = forecast["days"][0]
    return f"{{today['high']}}° / {{today['low']}}°, {{today['conditions']}}"
'''

FETCH_BEFORE = """def fetch_forecast(settings: Settings, city: str) -> dict:
    with urlopen(forecast_url(settings, city), timeout=settings.timeout) as response:
        return json.load(response)
"""

FETCH_AFTER = """def fetch_forecast(settings: Settings, city: str, cache: Cache) -> dict:
    if (cached := cache.get(city)) is not None:
        return cached
    while True:
        try:
            with urlopen(forecast_url(settings, city), timeout=settings.timeout) as response:
                forecast = json.load(response)
                break
        except URLError:
            time.sleep(1)
    cache.put(city, forecast)
    return forecast
"""

CONFIG = """from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    endpoint: str = "https://weather.example.com/v2"
    units: str = "metric"
    timeout: float = 10.0
{added}"""

CACHE = '''"""Remember forecasts for a while, so repeated lookups don't reach the service."""

import time


class Cache:
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        self.entries: dict[str, tuple[float, dict]] = {}

    def get(self, city: str) -> dict | None:
        stored = self.entries.get(city)
        if stored is None:
            return None
        saved_at, forecast = stored
        if time.time() - saved_at > self.seconds:
            return None
        return forecast

    def put(self, city: str, forecast: dict) -> None:
        self.entries[city] = (time.time(), forecast)
'''


def build_repository(root: Path) -> Scratch:
    """A small weather client on `main`, and a working tree that adds a cache and retries to it."""
    scratch = init_scratch(root)
    scratch.write("src/config.py", CONFIG.format(added=""))
    scratch.write("src/fetch.py", FETCH.format(imports="", cache_import="", fetch=FETCH_BEFORE))
    scratch.commit("Fetch forecasts")
    scratch.git("checkout", "--quiet", "-b", "add-cache")
    scratch.write("src/config.py", CONFIG.format(added="    cache_seconds: int = 600\n"))
    scratch.write(
        "src/fetch.py",
        FETCH.format(
            imports="import time\nfrom urllib.error import URLError\n",
            cache_import="from cache import Cache\n",
            fetch=FETCH_AFTER,
        ),
    )
    scratch.write("src/cache.py", CACHE)
    return scratch


BEAT = AdvanceClock(by=timedelta(milliseconds=600))
QUICK_BEAT = AdvanceClock(by=timedelta(milliseconds=150))
KEYSTROKE = AdvanceClock(by=timedelta(milliseconds=45))


def press(*keys: str, beat: AdvanceClock = BEAT) -> list[AutopilotStep]:
    """Each key, followed by a pause long enough to see what it did."""
    return [step for key in keys for step in (KeyPressed(key=key), beat)]


def write(text: str) -> list[AutopilotStep]:
    return [*paced(typed(text), KEYSTROKE), BEAT]


def hold(seconds: float) -> AdvanceClock:
    return AdvanceClock(by=timedelta(seconds=seconds))


WALKTHROUGH: list[AutopilotStep] = [
    hold(2),
    Marker("Comment on a range"),
    *press(Key.Tab, Key.Tab, Key.Tab, Key.Tab, Key.Tab),
    *press(*[Key.Down] * 6, beat=QUICK_BEAT),
    *press(*["J"] * 6, beat=QUICK_BEAT),
    *press(Key.Enter),
    *write("This retries forever while the service is down.\nCap the attempts, and back off between them?"),
    *press(Key.Escape),
    Marker("Comment on a file"),
    *press("f", Key.Down, Key.Enter, Key.Enter),
    *write("time.monotonic(), so a clock change can't expire everything at once? Same for #1."),
    *press(Key.Escape),
    Marker("Go to a comment"),
    *press("#", "1", Key.Enter),
    Marker("Split view"),
    *press("s"),
    hold(1.5),
    *press("s"),
    Marker("Syntax theme"),
    *press("t"),
    *write("monok"),
    *press(Key.Down, Key.Up, Key.Enter),
    Marker("Summary"),
    *press("r"),
    *write("Close: the cache is good, the retry loop needs a bound."),
    *press(Key.Escape, Key.Home),
    Marker("Copy as Markdown"),
    *press("c"),
    hold(3),
]


async def record(cast: Path) -> None:
    os.environ.update(GIT_ENVIRONMENT)
    with tempfile.TemporaryDirectory() as directory:
        scratch = build_repository(Path(directory) / "weather")
        repository = await find_repository(scratch.root)
        diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "HEAD"))
        started = Review.start(
            "demo", repository.common_dir, repository.worktree, repository.branch, BaseMode.HEAD, CLOCK
        )
        open_review = OpenReview(
            ReviewStore(Path(directory) / "reviews"),
            rename(started, "cache and retries"),
            None,
            lambda: "demo-fork",
            lambda: CLOCK,
        )
        preferences = PreferencesFile(Path(directory) / "preferences.json")
        await app(
            assess_app(repository, "main", open_review, preferences, preferences.load(), diff),
            headless=True,
            dimensions=DIMENSIONS,
            clock=FrozenClock(CLOCK),
            recording=Recording(cast),
            autopilot=[*WALKTHROUGH, Quit()],
        )


def draw(cast: str, directory: Path) -> None:
    """Draw the GIF from the asciicast's text with agg."""
    # agg 1.9.0 draws a no-break space, which the status line and key hints keep words together with, as `€`.
    # By the time a frame is drawn the lines are already wrapped, so a plain space draws the same screen.
    drawn = directory / "drawn.cast"
    drawn.write_text(cast.replace("\N{NO-BREAK SPACE}", " "))
    subprocess.run(["agg", "--quiet", "--font-size", "14", str(drawn), str(GIF)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--redraw", action="store_true", help="Draw the GIF even if the asciicast hasn't changed.")
    arguments = parser.parse_args()
    new_paths = [str(path) for path in (CAST, GIF) if not path.exists()]
    with tempfile.TemporaryDirectory() as directory:
        recorded = Path(directory) / "demo.cast"
        asyncio.run(record(recorded))
        cast = recorded.read_text()
        is_current = not new_paths and CAST.read_text() == cast
        if is_current and not arguments.redraw:
            return
        CAST.write_text(cast)
        draw(cast, Path(directory))
    # pre-commit only fails a hook that leaves an unstaged diff, which a new untracked file isn't.
    if new_paths:
        subprocess.run(["git", "add", "--intent-to-add", *new_paths], check=True)


if __name__ == "__main__":
    main()
