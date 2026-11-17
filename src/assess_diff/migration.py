"""
Bringing a file assess-diff saved (a review, the preferences) up to the version this assess-diff writes.

Each model that's written to disk declares its own versions and migrations;
this is the loop they share, so they agree on what counts as a version and when there's no way forward.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping

type Migration = Callable[[Mapping[str, object]], dict[str, object]]
"""A file's document at one version, rewritten as the next version would have written it."""


class UnknownVersion(Exception):
    """
    A document at a version there's no migration from: a newer assess-diff's, or no version at all.
    Its message reads on after "This review is", as `parse_review` words it.
    """


def migrated(document: Mapping[str, object], migrations: Mapping[int, Migration], current: int) -> Mapping[str, object]:
    """
    `document` migrated, one version at a time, to version `current`.

    Raises:
        UnknownVersion: If it reaches a version that isn't `current` and has no migration in `migrations`.
    """
    # `type(...) is int` rather than `isinstance`, and checked even at the current version,
    # since JSON's `true` and `1.0` compare equal to 1 (and `true` hashes as 1 in `migrations`), but no version wrote them.
    while type(version := document.get("version")) is not int or version != current:
        migrate = migrations.get(version) if type(version) is int else None
        if migrate is None:
            raise UnknownVersion(f"version {version!r}, and this assess-diff reads up to version {current}")
        document = migrate(document)
    return document
