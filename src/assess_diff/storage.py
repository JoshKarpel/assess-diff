"""
Reviews on disk: one JSON file per review in a directory, written atomically.

This is the only module that reads or writes review files.

Files rather than SQLite, since nothing here needs relational queries or transactions across reviews:
a review is only ever written by the one process that has it open, two processes in two worktrees never touch
the same file, and grouping by project is a pass over a directory of small files.
What that gives up is two processes opening the _same_ review, which `ReviewStore.save` guards against.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from assess_diff.review import Review
from assess_diff.review import UnreadableReview
from assess_diff.review import parse_review


class ReviewChanged(Exception):
    """A save found the review's file changed since it was read, so another process is writing it too."""


class NoSuchReview(Exception):
    """No review, or more than one, has an id starting with what was asked for."""


def reviews_directory(data_home: Path | None) -> Path:
    """Where reviews live: under `$XDG_DATA_HOME`, or `~/.local/share` where that's unset, as the XDG spec says."""
    return (data_home or Path.home() / ".local" / "share") / "assess-diff" / "reviews"


def parsed(path: Path, content: bytes) -> Review:
    """
    The review in `content`, read from the file at `path`.

    Raises:
        UnreadableReview: If it can't be read as a review, naming the file, so it can be fixed or moved aside.
    """
    try:
        return parse_review(content)
    except (UnreadableReview, ValueError) as error:
        raise UnreadableReview(f"Can't read the review in {path}: {error}") from error


def digest(content: bytes) -> str:
    """
    What a save compares to tell whether another process wrote the file:
    not its modification time, which two writes within one clock tick share.
    """
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class Stored:
    """A review as last read from or written to disk, with a digest of the file's content then."""

    review: Review
    digest: str


@dataclass(frozen=True, slots=True)
class ReviewStore:
    directory: Path

    def path(self, review_id: str) -> Path:
        return self.directory / f"{review_id}.json"

    def reviews(self) -> tuple[Review, ...]:
        """Every review, most recently updated first."""
        reviews = (parsed(path, path.read_bytes()) for path in self.directory.glob("*.json"))
        return tuple(sorted(reviews, key=lambda review: review.updated, reverse=True))

    def find(self, prefix: str) -> Stored:
        """
        The review whose id starts with `prefix`, as git takes an abbreviated commit.

        Raises:
            NoSuchReview: If no review's id starts with `prefix`, or more than one does.
        """
        matches = sorted(self.directory.glob(f"{prefix}*.json"))
        if len(matches) != 1:
            found = "No review" if not matches else f"{len(matches)} reviews"
            raise NoSuchReview(f"{found} with an id starting with {prefix!r}")
        return self.load(matches[0].stem)

    def load(self, review_id: str) -> Stored:
        path = self.path(review_id)
        content = path.read_bytes()
        return Stored(review=parsed(path, content), digest=digest(content))

    @contextmanager
    def locked(self) -> Iterator[None]:
        """
        Holds the store's lock, which every save holds across its check and its write,
        so two processes saving one review take turns rather than both passing the check.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def save(self, review: Review, previous: str | None) -> Stored:
        """
        Write `review`, replacing its file in one step, so a crash mid-write leaves the last save whole.

        `previous` is the digest of what this process last read or wrote, or `None` for a review it has never saved.

        Raises:
            ReviewChanged: If the file isn't what this process last read or wrote,
                so saving would drop whatever another process wrote.
        """
        path = self.path(review.id)
        content = review.model_dump_json(indent=2).encode()
        with self.locked():
            current = digest(path.read_bytes()) if path.exists() else None
            if current != previous:
                raise ReviewChanged(f"Review {review.id} was changed by another process")
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        return Stored(review=review, digest=digest(content))

    def delete(self, review_id: str) -> None:
        self.path(review_id).unlink()


def for_project(reviews: tuple[Review, ...], project: Path, worktree: Path) -> tuple[Review, ...]:
    """The reviews of `project`, those of `worktree` first, each group in the order given."""
    mine = tuple(review for review in reviews if review.project == project)
    return (
        *(review for review in mine if review.worktree == worktree),
        *(review for review in mine if review.worktree != worktree),
    )


@dataclass(slots=True)
class OpenReview:
    """
    The review this process has open.
    Each update is written to disk before the UI shows it, so a crash or a stray Ctrl+C loses nothing typed.
    """

    store: ReviewStore
    review: Review
    digest: str | None
    """What this process last read or wrote, or `None` before the review's first save."""
    new_id: Callable[[], str]
    now: Callable[[], datetime]

    def switch(self, review: Review, digest: str | None) -> None:
        """Make `review` the open one: a review read from disk with its digest, or a new one with `None`."""
        self.review = review
        self.digest = digest

    def update(self, edit: Callable[[Review], Review]) -> str | None:
        """
        Apply `edit` and save the result, returning a notice for the reader when the save had to go elsewhere:
        if another process changed the review's file, this one's version is saved as a new review instead,
        so neither process's comments are lost.
        """
        review = edit(self.review).model_copy(update={"updated": self.now()})
        notice = None
        try:
            stored = self.store.save(review, self.digest)
        except ReviewChanged:
            forked = review.model_copy(update={"id": self.new_id()})
            stored = self.store.save(forked, None)
            notice = f"Another assess-diff changed review {review.id}, so this one is saved as {forked.id}"
        self.review = stored.review
        self.digest = stored.digest
        return notice
