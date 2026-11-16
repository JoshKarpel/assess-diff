"""
The reader's preferences, kept apart from any review: how the diff is laid out and colored.
They're configuration rather than data, so they live under `$XDG_CONFIG_HOME`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator
from pygments.styles import get_all_styles

from assess_diff.navigation import Scope
from assess_diff.navigation import ViewMode

DEFAULT_THEME = "github-dark"


def themes() -> tuple[str, ...]:
    """Every syntax highlighting theme, by the name Pygments knows it by."""
    return tuple(sorted(get_all_styles()))


class Preferences(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal[2]
    view_mode: ViewMode
    scope: Scope
    theme: str

    @field_validator("theme")
    @classmethod
    def theme_exists(cls, theme: str) -> str:
        if theme not in themes():
            raise ValueError(f"{theme!r} isn't a syntax highlighting theme; choose one of {', '.join(themes())}")
        return theme

    @classmethod
    def defaults(cls) -> Preferences:
        return cls(version=CURRENT_VERSION, view_mode=ViewMode.UNIFIED, scope=Scope.CONTEXT, theme=DEFAULT_THEME)


CURRENT_VERSION = 2

type Migration = Callable[[Mapping[str, object]], dict[str, object]]
"""A preferences file's document at one version, rewritten as the next version would have written it."""


def from_version_1(document: Mapping[str, object]) -> dict[str, object]:
    """Version 2 adds `scope`, which a version 1 file takes at its default: hunks and their context."""
    # A literal rather than `Scope.CONTEXT`, so a version 1 file migrates the same however `Scope` changes.
    return {**document, "version": 2, "scope": "context"}


# Each version's migration to the next, by the version it migrates from.
MIGRATIONS: Mapping[int, Migration] = {1: from_version_1}


def parse_preferences(content: bytes) -> Preferences:
    """
    Preferences from a file's content, migrated to the current version if an older one wrote it.

    Raises:
        pydantic.ValidationError: If it isn't preferences this version reads, a newer version's included.
    """
    document = json.loads(content)
    # `type(...) is int`, since JSON's `true` and `1.0` equal 1 (and `true` hashes as 1), but no version wrote them.
    while (
        isinstance(document, dict)
        and type(version := document.get("version")) is int
        and (migrate := MIGRATIONS.get(version)) is not None
    ):
        document = migrate(document)
    return Preferences.model_validate(document)


def preferences_path(config_home: Path | None) -> Path:
    """Where preferences live: under `$XDG_CONFIG_HOME`, or `~/.config` where that's unset, as the XDG spec says."""
    return (config_home or Path.home() / ".config") / "assess-diff" / "preferences.json"


@dataclass(frozen=True, slots=True)
class PreferencesFile:
    path: Path

    def load(self) -> Preferences:
        """The saved preferences, or the defaults if none have been saved yet."""
        if not self.path.exists():
            return Preferences.defaults()
        return parse_preferences(self.path.read_bytes())

    def save(self, preferences: Preferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(preferences.model_dump_json(indent=2))
        temporary.replace(self.path)
