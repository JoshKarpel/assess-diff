from __future__ import annotations

import gc
import os
import pty
import resource
import subprocess
import sys
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import TextIO

import pytest


def pytest_configure(config: pytest.Config) -> None:
    # A mutant can turn a loop that must advance into one that allocates forever, and mutmut limits only CPU time,
    # so cap each mutant's child relative to its size when it forks; a capped runaway fails as killed.
    if "__mutmut_" in os.environ.get("MUTANT_UNDER_TEST", "") and sys.platform == "linux":  # pragma: no cover
        # Only mutmut's children run this, never the suite on its own, so coverage can't see it.
        pages = int(Path("/proc/self/statm").read_text().split()[0])
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (pages * resource.getpagesize() + (1 << 30), hard))


@pytest.fixture(autouse=True)
def unfrozen_heap() -> Iterator[None]:
    """Hands the objects the app froze back to the garbage collector, so no test runs on a heap another froze."""
    yield
    gc.unfreeze()


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the developer's git config (a global ignore file, signing, aliases) out of every git the tests run."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # git reads `$XDG_CONFIG_HOME/git/ignore` (or `~/.config/git/ignore`) even with no global config.
    monkeypatch.setenv("XDG_CONFIG_HOME", os.devnull)
    monkeypatch.setenv("HOME", os.devnull)


@dataclass(frozen=True, slots=True)
class Scratch:
    """A real git repository in a temporary directory, for tests that run git."""

    root: Path

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def write(self, path: str, content: str | bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)

    def commit(self, message: str = "commit") -> str:
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def empty_commit(self, message: str) -> str:
        """A commit of nothing, which moves `HEAD` and leaves the working tree's changes uncommitted."""
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


def init_scratch(root: Path) -> Scratch:
    root.mkdir(parents=True, exist_ok=True)
    scratch = Scratch(root)
    scratch.git("init", "--quiet", "--initial-branch=main")
    scratch.git("config", "user.email", "assess@example.com")
    scratch.git("config", "user.name", "assess")
    scratch.git("config", "commit.gpgsign", "false")
    return scratch


@pytest.fixture
def scratch(tmp_path: Path) -> Scratch:
    return init_scratch(tmp_path / "repo")


@dataclass(frozen=True, slots=True)
class Terminal:
    """
    A pseudo-terminal: the program reads `stream` and writes `screen`, both its side,
    and the user side types on `keyboard`.
    """

    stream: TextIO
    screen: TextIO
    keyboard: int


def read_until_closed(keyboard: int) -> None:
    """
    Reads everything the program side of a pseudo-terminal writes, as a terminal emulator would, until it closes,
    so a program that waits for its output to drain never blocks.
    """
    # Linux raises EIO once the program side closes, rather than reading the end of the input.
    with suppress(OSError):
        while os.read(keyboard, 2**10):  # pragma: no branch
            pass


@pytest.fixture
def terminal() -> Iterator[Terminal]:
    keyboard, program = pty.openpty()
    emulator = Thread(target=read_until_closed, args=(keyboard,))
    emulator.start()
    with os.fdopen(program) as stream, os.fdopen(os.dup(program), "w") as screen:
        yield Terminal(stream=stream, screen=screen, keyboard=keyboard)
    emulator.join()
    os.close(keyboard)
