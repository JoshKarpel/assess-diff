from __future__ import annotations

import io
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from without_cli import Streams
from without_cli import run

from assess_diff.cli import cli
from assess_diff.cli import latest
from assess_diff.cli import main
from assess_diff.cli import now
from assess_diff.cli import target_branch
from assess_diff.cli import with_default_command
from assess_diff.diff import BaseMode
from assess_diff.diff import LineKind
from assess_diff.git import find_repository
from assess_diff.review import Review
from assess_diff.storage import ReviewStore
from tests.builders import STARTED
from tests.builders import comment
from tests.conftest import Scratch
from tests.conftest import Terminal


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], ["review"]),
        (["--new"], ["review", "--new"]),
        (["--data-home", "/d", "--review", "x"], ["--data-home", "/d", "review", "--review", "x"]),
        (["--config-home", "/c", "list"], ["--config-home", "/c", "list"]),
        (["--data-home=/d", "list"], ["--data-home=/d", "list"]),
        (["--config-home=/c", "--new"], ["--config-home=/c", "review", "--new"]),
        (["--data-home=/d", "--config-home", "/c", "list"], ["--data-home=/d", "--config-home", "/c", "list"]),
        (
            ["--data-home", "/d", "--config-home=/c", "--new"],
            ["--data-home", "/d", "--config-home=/c", "review", "--new"],
        ),
        (["show", "abc"], ["show", "abc"]),
        (["delete", "abc"], ["delete", "abc"]),
        (["review", "--new"], ["review", "--new"]),
        (["--help"], ["--help"]),
        (["-h"], ["-h"]),
        (["--version"], ["--version"]),
    ],
)
def test_with_default_command_opens_the_app_unless_a_command_is_named(argv: list[str], expected: list[str]) -> None:
    assert with_default_command(argv) == expected


def test_now_is_in_utc() -> None:
    assert now().utcoffset() == timedelta(0)


@pytest.mark.parametrize(("base", "expected"), [("release/9", "release/9"), (None, "main")])
async def test_target_branch_is_the_base_given_or_else_the_default_branch(
    scratch: Scratch, base: str | None, expected: str
) -> None:
    scratch.commit()

    assert await target_branch(await find_repository(scratch.root), base) == expected


@pytest.fixture
def project(scratch: Scratch, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReviewStore:
    """The scratch repository as the working directory, with `one = 1` changed to `one = 2`, and its review store."""
    scratch.write("app.py", "one = 1\n")
    scratch.commit()
    scratch.write("app.py", "one = 2\n")
    monkeypatch.chdir(scratch.root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return ReviewStore(tmp_path / "data" / "assess-diff" / "reviews")


def seed(store: ReviewStore, scratch: Scratch, review_id: str, name: str, body: str = "") -> Review:
    review = Review.start(review_id, scratch.root / ".git", scratch.root, "main", BaseMode.HEAD, STARTED)
    review = review.model_copy(update={"name": name})
    if body:
        review = review.model_copy(
            update={
                "comments": (comment(1, "app.py", 1, 1, ((LineKind.ADDED, "one = 2"),), body=body),),
                "next_number": 2,
            }
        )
    return store.save(review, None).review


def invoke(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(
        cli,
        argv=with_default_command(argv),
        env=dict(os.environ),
        streams=Streams(stdin=io.StringIO(), stdout=out, stderr=err),
    )
    return code, out.getvalue(), err.getvalue()


async def test_latest_is_this_worktrees_review_even_with_newer_ones_elsewhere(
    project: ReviewStore, scratch: Scratch
) -> None:
    seed(project, scratch, "0199aaaa-mine", "mine")
    newer = STARTED + timedelta(hours=1)
    other_worktree = scratch.root.parent / "other-worktree"
    other_project = scratch.root.parent / "other-project" / ".git"
    project.save(Review.start("0199bbbb-there", scratch.root / ".git", other_worktree, "x", BaseMode.HEAD, newer), None)
    project.save(Review.start("0199cccc-theirs", other_project, scratch.root, "y", BaseMode.HEAD, newer), None)

    found = latest(project, await find_repository(scratch.root))

    assert found is not None
    assert found.review.id == "0199aaaa-mine"


def test_list_shows_this_projects_reviews(project: ReviewStore, scratch: Scratch) -> None:
    seed(project, scratch, "0199cccc-first", "first look", body="hm")

    code, out, _ = invoke("list")

    assert code == 0
    assert out.startswith("* 0199cccc-fir  first look  main  1 comment  updated ")
    assert out.endswith(f"  {scratch.root}\n")


def test_show_prints_the_latest_review_as_markdown(project: ReviewStore, scratch: Scratch) -> None:
    seed(project, scratch, "0199cccc-first", "first look", body="Why two?")

    code, out, _ = invoke("show")

    assert code == 0
    assert out.startswith("# Review of `main` against `HEAD` (`")
    assert out.endswith("## 1. `app.py:1`\n\n```diff\n+one = 2\n```\n\nWhy two?\n")


def test_show_takes_an_id(project: ReviewStore, scratch: Scratch) -> None:
    seed(project, scratch, "0199cccc-first", "one", body="first body")
    seed(project, scratch, "0199dddd-second", "two", body="second body")

    _, out, _ = invoke("show", "0199cccc")

    assert out.endswith("first body\n")


def test_show_without_reviews_says_how_to_start_one(project: ReviewStore) -> None:
    assert invoke("show") == (1, "", "This worktree has no reviews yet; `assess-diff` starts one.\n")


def test_delete_removes_the_review(project: ReviewStore, scratch: Scratch) -> None:
    seed(project, scratch, "0199cccc-doomed", "doomed")

    assert invoke("delete", "0199cc") == (0, "Deleted review 0199cccc-doomed\n", "")
    assert project.reviews() == ()


@pytest.mark.parametrize("arguments", [["delete", "nope"], ["--review", "nope"]], ids=["named", "default"])
def test_main_reports_an_unknown_review(
    project: ReviewStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["assess-diff", *arguments])

    with pytest.raises(SystemExit) as exited:
        main()

    assert (exited.value.code, capsys.readouterr().err) == (
        1,
        "assess-diff: No review with an id starting with 'nope'\n",
    )


def test_main_runs_the_command_line(
    project: ReviewStore, scratch: Scratch, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(project, scratch, "0199cccc-first", "named")
    monkeypatch.setattr(sys, "argv", ["assess-diff", "list"])

    with pytest.raises(SystemExit) as exited:
        main()

    assert (exited.value.code, "named" in capsys.readouterr().out) == (0, True)


def quit_at_once(terminal: Terminal, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs the app on `terminal` with `q` already typed, so it quits as soon as it reads input."""
    monkeypatch.setattr(sys, "stdin", terminal.stream)
    monkeypatch.setattr(sys, "stdout", terminal.screen)
    os.write(terminal.keyboard, b"q")


def test_review_resumes_this_worktrees_latest_review(
    project: ReviewStore, scratch: Scratch, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(project, scratch, "0199cccc-first", "resumed")
    quit_at_once(terminal, monkeypatch)

    code, out, _ = invoke()

    assert (code, out) == (0, "Saved review “resumed” (0199cccc-fir); `assess-diff show` prints it.\n")


def test_review_new_starts_a_review_that_is_saved_only_once_written(
    project: ReviewStore, scratch: Scratch, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(project, scratch, "0199cccc-first", "old")
    quit_at_once(terminal, monkeypatch)

    code, out, _ = invoke("review", "--new", "--base", "main")

    assert (code, out) == (0, "Nothing was written, so no review was saved.\n")
    assert [review.id for review in project.reviews()] == ["0199cccc-first"]


def test_review_opens_a_review_by_id(
    project: ReviewStore, scratch: Scratch, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(project, scratch, "0199cccc-first", "chosen")
    seed(project, scratch, "0199dddd-second", "not chosen")
    quit_at_once(terminal, monkeypatch)

    _, out, _ = invoke("review", "--review", "0199cccc")

    assert "“chosen”" in out


def test_review_in_a_repository_without_a_default_branch_still_opens(
    project: ReviewStore, scratch: Scratch, terminal: Terminal, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch.git("branch", "-m", "main", "trunk")
    quit_at_once(terminal, monkeypatch)

    assert invoke()[0] == 0


def test_main_reports_an_unreadable_review(
    project: ReviewStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project.directory.mkdir(parents=True)
    (project.directory / "future.json").write_text('{"version": 99}')
    monkeypatch.setattr(sys, "argv", ["assess-diff", "list"])

    with pytest.raises(SystemExit) as exited:
        main()

    assert (exited.value.code, capsys.readouterr().err) == (
        1,
        f"assess-diff: Can't read the review in {project.directory / 'future.json'}: "
        "This review is version 99, and this assess-diff reads up to version 2\n",
    )
