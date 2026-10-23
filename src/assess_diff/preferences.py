"""
The reader's preferences, kept apart from any review: how the diff is laid out and colored.
They're configuration rather than data, so they live under `$XDG_CONFIG_HOME`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator
from pygments.styles import get_all_styles

from assess_diff.navigation import ViewMode

DEFAULT_THEME = "github-dark"


def themes() -> tuple[str, ...]:
    """Every syntax highlighting theme, by the name Pygments knows it by."""
    return tuple(sorted(get_all_styles()))


class Preferences(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal[1]
    view_mode: ViewMode
    theme: str

    @field_validator("theme")
    @classmethod
    def theme_exists(cls, theme: str) -> str:
        if theme not in themes():
            raise ValueError(f"{theme!r} isn't a syntax highlighting theme; choose one of {', '.join(themes())}")
        return theme

    @classmethod
    def defaults(cls) -> Preferences:
        return cls(version=1, view_mode=ViewMode.UNIFIED, theme=DEFAULT_THEME)


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
        return Preferences.model_validate_json(self.path.read_bytes())

    def save(self, preferences: Preferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(preferences.model_dump_json(indent=2))
        temporary.replace(self.path)
