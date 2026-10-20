from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from assess_diff.navigation import ViewMode
from assess_diff.preferences import Preferences
from assess_diff.preferences import PreferencesFile
from assess_diff.preferences import preferences_path
from assess_diff.preferences import themes


def test_preferences_path_is_under_the_config_home() -> None:
    assert preferences_path(Path("/config")) == Path("/config/assess-diff/preferences.json")


def test_preferences_path_defaults_to_dot_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert preferences_path(None) == tmp_path / ".config" / "assess-diff" / "preferences.json"


def test_defaults_are_unified_in_github_dark() -> None:
    assert Preferences.defaults() == Preferences(version=1, view_mode=ViewMode.UNIFIED, theme="github-dark")


def test_load_without_a_file_is_the_defaults(tmp_path: Path) -> None:
    assert PreferencesFile(tmp_path / "missing.json").load() == Preferences.defaults()


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    file = PreferencesFile(tmp_path / "nested" / "preferences.json")
    chosen = Preferences(version=1, view_mode=ViewMode.SPLIT, theme="monokai")

    file.save(chosen)

    assert (file.load(), sorted(path.name for path in file.path.parent.iterdir())) == (chosen, ["preferences.json"])


def test_an_unknown_theme_fails_to_parse() -> None:
    with pytest.raises(ValidationError, match="'no-such-theme' isn't a syntax highlighting theme"):
        Preferences(version=1, view_mode=ViewMode.UNIFIED, theme="no-such-theme")


def test_themes_are_sorted_and_include_the_default() -> None:
    names = themes()

    assert (list(names) == sorted(names), "github-dark" in names) == (True, True)
