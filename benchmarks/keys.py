"""
How long the app takes to answer a key, on generated diffs of a few sizes.

Each workload starts the whole app headless on a generated repository, as the tests do,
then presses one key over and over, timing each press from the render before it to the render after it.
The repositories are the same on every run, so runs on one machine compare.
`just bench` runs them all; `just profile` runs one under the sampling profiler.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

from counterweight import __version__ as counterweight_version
from counterweight.autopilot import AutopilotStep
from counterweight.autopilot import WaitFor
from counterweight.events import KeyPressed
from counterweight.keys import Key

from assess_diff.preferences import PreferencesFile
from assess_diff.storage import ReviewStore
from tests.conftest import Scratch
from tests.conftest import init_scratch
from tests.harness import Harness

FUNCTIONS_PER_FILE = 667
"""Three lines each, so a file is about 2,000 lines."""

CHANGED_EVERY = 50
"""Every this many functions one changes, so each file has a hunk about every 150 lines."""

WIDE_TERMS = 80
"""A changed function returns a sum of this many terms, a line 400 to 570 cells wide, so the diff scrolls sideways."""


@dataclass(frozen=True, slots=True)
class Size:
    name: str
    files: int


SIZES = (Size("small", 1), Size("medium", 5), Size("large", 20))


@dataclass(frozen=True, slots=True)
class Workload:
    name: str
    before: tuple[KeyPressed, ...]
    """Keys pressed before the timing starts."""
    key: KeyPressed
    presses: int


WORKLOADS = (
    Workload("down", (), KeyPressed(key=Key.Down), 60),
    Workload("tab", (), KeyPressed(key=Key.Tab), 30),
    Workload("page-down", (), KeyPressed(key=Key.PageDown), 20),
    # Into the first hunk's new comment, where every key is an edit saved to disk.
    Workload("type", (KeyPressed(key=Key.Tab), KeyPressed(key=Key.Enter)), KeyPressed(key="x"), 40),
    # Short of where the widest line's end reaches the right edge, so every press scrolls.
    Workload("right", (), KeyPressed(key=Key.Right), 30),
)


def source(index: int, is_changed: bool) -> str:
    """
    The functions of file `index`, named for it, so no line is in two files:
    rows alike across files would share the app's cached rows, as a real diff's files don't.
    """

    def returned(n: int) -> str:
        if is_changed and n % CHANGED_EVERY == 0:
            return " + ".join(str(n * 2 + term) for term in range(WIDE_TERMS))
        return str(n)

    return "".join(f"def file{index}_f{n}(x):\n    return x + {returned(n)}\n\n" for n in range(FUNCTIONS_PER_FILE))


def build_repository(root: Path, size: Size) -> Scratch:
    """A repository of `size.files` files, committed, then changed in the working tree."""
    scratch = init_scratch(root)
    paths = [f"pkg/module_{index:02}.py" for index in range(size.files)]
    for index, path in enumerate(paths):
        scratch.write(path, source(index, is_changed=False))
    scratch.commit()
    for index, path in enumerate(paths):
        scratch.write(path, source(index, is_changed=True))
    return scratch


async def press_times(scratch: Scratch, workload: Workload) -> list[float]:
    """Seconds from the render before each press to the render after it, in a fresh app with a fresh review."""
    stamps: list[float] = []

    def stamp(_: object) -> bool:
        stamps.append(time.perf_counter())
        return True

    rendered = WaitFor(stamp, timeout=timedelta(minutes=5))
    steps: list[AutopilotStep] = [*workload.before, rendered]
    for _ in range(workload.presses):
        steps += [workload.key, rendered]
    with tempfile.TemporaryDirectory() as data:
        harness = Harness(
            scratch=scratch,
            store=ReviewStore(Path(data) / "reviews"),
            preferences=PreferencesFile(Path(data) / "preferences.json"),
        )
        await harness.run(steps)
    return [after - before for before, after in pairwise(stamps)]


def say(line: str) -> None:
    """Writes a line of the report as soon as it's known, since a full run takes minutes."""
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def milliseconds(seconds: float) -> str:
    return f"{seconds * 1000:8.1f}"


async def run(only: str | None, repeat: int) -> None:
    say(
        f"Python {platform.python_version()}, counterweight {counterweight_version}, "
        f"{os.cpu_count()} CPUs, {repeat} runs of each\n"
    )
    say(f"{'workload':<20}{'presses':>8}{'median ms':>11}{'p90 ms':>9}{'max ms':>9}")
    for size in SIZES:
        workloads = [w for w in WORKLOADS if only is None or only == f"{size.name}/{w.name}"]
        if not workloads:
            continue
        with tempfile.TemporaryDirectory() as directory:
            scratch = build_repository(Path(directory) / "repo", size)
            for workload in workloads:
                times = [t for _ in range(repeat) for t in await press_times(scratch, workload)]
                p90 = statistics.quantiles(times, n=10)[-1]
                say(
                    f"{size.name + '/' + workload.name:<20}{len(times):>8}"
                    f"   {milliseconds(statistics.median(times))} {milliseconds(p90)} {milliseconds(max(times))}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    names = [f"{size.name}/{workload.name}" for size in SIZES for workload in WORKLOADS]
    parser.add_argument("--only", choices=names, help="run just this workload")
    parser.add_argument("--repeat", type=int, default=3, help="how many fresh runs of each workload to pool")
    arguments = parser.parse_args()
    # As the tests do, so the developer's git config (a global ignore file, hooks, signing) can't change the diff.
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    asyncio.run(run(arguments.only, arguments.repeat))


if __name__ == "__main__":
    main()
