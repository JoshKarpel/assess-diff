from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from assess_diff.navigation import Scope
from assess_diff.navigation import ViewMode
from assess_diff.preferences import Preferences
from assess_diff.preferences import PreferencesFile
from assess_diff.preferences import parse_preferences
from assess_diff.preferences import preferences_path
from assess_diff.preferences import themes


def test_preferences_path_is_under_the_config_home() -> None:
    assert preferences_path(Path("/config")) == Path("/config/assess-diff/preferences.json")


def test_preferences_path_defaults_to_dot_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert preferences_path(None) == tmp_path / ".config" / "assess-diff" / "preferences.json"


FIXTURES = Path(__file__).parent / "fixtures"


def test_defaults_are_unified_hunks_in_github_dark() -> None:
    assert Preferences.defaults() == Preferences(
        version=2, view_mode=ViewMode.UNIFIED, scope=Scope.CONTEXT, theme="github-dark"
    )


def test_load_without_a_file_is_the_defaults(tmp_path: Path) -> None:
    assert PreferencesFile(tmp_path / "missing.json").load() == Preferences.defaults()


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    # Two directories that don't exist yet, as `~/.config/assess-diff` on a machine without `~/.config`.
    file = PreferencesFile(tmp_path / "config" / "assess-diff" / "preferences.json")
    chosen = Preferences(version=2, view_mode=ViewMode.SPLIT, scope=Scope.WHOLE, theme="monokai")

    file.save(chosen)

    assert (file.load(), sorted(path.name for path in file.path.parent.iterdir())) == (chosen, ["preferences.json"])


def test_save_writes_json_indented_for_reading(tmp_path: Path) -> None:
    file = PreferencesFile(tmp_path / "preferences.json")

    file.save(Preferences(version=2, view_mode=ViewMode.SPLIT, scope=Scope.WHOLE, theme="monokai"))

    assert file.path.read_text() == (
        '{\n  "version": 2,\n  "view_mode": "split",\n  "scope": "whole",\n  "theme": "monokai"\n}'
    )


def test_a_version_1_file_loads_with_hunks_and_its_other_choices_kept() -> None:
    loaded = PreferencesFile(FIXTURES / "preferences-v1.json").load()

    assert loaded == Preferences(version=2, view_mode=ViewMode.SPLIT, scope=Scope.CONTEXT, theme="monokai")


@pytest.mark.parametrize(
    "version",
    ["3", "true", "1.0", '"1"'],
    ids=["newer", "true", "float", "string"],
)
def test_a_version_no_migration_reads_fails_to_parse(version: str) -> None:
    content = f'{{"version": {version}, "view_mode": "split", "scope": "whole", "theme": "monokai"}}'

    with pytest.raises(ValidationError, match="version"):
        parse_preferences(content.encode())


def test_preferences_that_arent_an_object_fail_to_parse() -> None:
    with pytest.raises(ValidationError):
        parse_preferences(b"[]")


def test_an_unknown_theme_fails_to_parse() -> None:
    with pytest.raises(ValidationError, match="'no-such-theme' isn't a syntax highlighting theme"):
        Preferences(version=2, view_mode=ViewMode.UNIFIED, scope=Scope.CONTEXT, theme="no-such-theme")


def test_themes_are_sorted_and_include_the_default() -> None:
    names = themes()

    assert (list(names) == sorted(names), "github-dark" in names) == (True, True)
