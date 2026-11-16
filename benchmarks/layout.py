"""
How long laying out a diff takes, on the generated repositories `benchmarks.keys` presses keys in.

The app lays the diff out again whenever it reloads, a comment is added or removed, or the view or scope changes,
so this cost sits under those keys; timing it alone shows how it grows with the diff and with comments on it,
which the key benchmarks, in whole files and with a comment or none, don't.
Each layout is timed with comments spread through each file's unchanged lines, as well as with none,
since in the folded view the lines a comment is on stay in view, so comments make that view more work to lay out.
`just bench-layout` runs them all.
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
from datetime import UTC
from datetime import datetime
from pathlib import Path

from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.git import compute_diff
from assess_diff.git import find_repository
from assess_diff.git import resolve_base
from assess_diff.navigation import Scope
from assess_diff.navigation import ViewMode
from assess_diff.navigation import build_document
from assess_diff.review import Comment
from assess_diff.review import anchor_for
from benchmarks.keys import SIZES
from benchmarks.keys import Size
from benchmarks.keys import build_repository

COMMENTS_PER_FILE = (0, 20)
"""None, and about one every 100 lines of a generated file, each on one unchanged line."""

LAYOUTS = 20
"""How many times each layout is timed."""


@dataclass(frozen=True, slots=True)
class Case:
    size: Size
    mode: ViewMode
    scope: Scope
    comments: int

    @property
    def name(self) -> str:
        return f"{self.size.name}/{self.mode}/{self.scope}/{self.comments}"


CASES = tuple(
    Case(size, mode, scope, comments)
    for size in SIZES
    for mode in ViewMode
    for scope in Scope
    for comments in COMMENTS_PER_FILE
)


def spread_comments(diff: Diff, per_file: int) -> tuple[Comment, ...]:
    """`per_file` comments on each file of `diff`, each on one unchanged line, spaced evenly down the file."""
    created = datetime(2026, 1, 1, tzinfo=UTC)
    comments: list[Comment] = []
    for file in diff.files:
        unchanged = [line for line in file.lines if line.kind is LineKind.CONTEXT]
        for index in range(per_file):
            line = unchanged[(index * 2 + 1) * len(unchanged) // (per_file * 2)]
            anchor, excerpt = anchor_for(file, (line,), Side.NEW, diff.base.commit)
            comments.append(Comment(number=len(comments) + 1, anchor=anchor, excerpt=excerpt, body="", created=created))
    return tuple(comments)


async def load_diff(root: Path) -> Diff:
    repository = await find_repository(root)
    return await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))


def layout_times(diff: Diff, case: Case) -> list[float]:
    comments = spread_comments(diff, case.comments)
    times: list[float] = []
    for _ in range(LAYOUTS):
        start = time.perf_counter()
        build_document(diff, comments, case.mode, case.scope)
        times.append(time.perf_counter() - start)
    return times


def say(line: str) -> None:
    """Writes a line of the report as soon as it's known, since a full run takes a while."""
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def milliseconds(seconds: float) -> str:
    return f"{seconds * 1000:8.1f}"


def run(only: str | None) -> None:
    say(f"Python {platform.python_version()}, {os.cpu_count()} CPUs, {LAYOUTS} layouts of each\n")
    say(f"{'case':<32}{'median ms':>11}{'max ms':>9}")
    for size in SIZES:
        cases = [case for case in CASES if case.size == size and (only is None or only == case.name)]
        if not cases:
            continue
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            build_repository(root, size)
            diff = asyncio.run(load_diff(root))
            for case in cases:
                times = layout_times(diff, case)
                say(f"{case.name:<32}   {milliseconds(statistics.median(times))} {milliseconds(max(times))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=[case.name for case in CASES], help="run just this case")
    arguments = parser.parse_args()
    # As the tests do, so the developer's git config (a global ignore file, hooks, signing) can't change the diff.
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    run(arguments.only)


if __name__ == "__main__":
    main()
