from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime

import pytest
from pydantic import ValidationError

from assess_diff.diff import BaseMode
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.review import ExcerptLine
from assess_diff.review import NewAnchor
from assess_diff.review import OldAnchor
from assess_diff.review import UnreadableReview
from assess_diff.review import add_comment
from assess_diff.review import anchor_for
from assess_diff.review import anchored_lines
from assess_diff.review import default_name
from assess_diff.review import delete_comment
from assess_diff.review import edit_comment
from assess_diff.review import edit_summary
from assess_diff.review import is_outdated
from assess_diff.review import parse_review
from tests.builders import BASE_COMMIT
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import diff_of
from tests.builders import file_comment
from tests.builders import modified
from tests.builders import removed
from tests.builders import review

LATER = datetime(2026, 4, 1, 9, 30, tzinfo=UTC)
FILE = modified(
    "src/app.py",
    context(1, 1, "def handler():"),
    removed(2, "    return None"),
    added(2, "    return compute()"),
    added(3, "    # computed"),
    context(3, 4, ""),
    removed(4, "old tail", hunk=1),
)


def test_add_comment_numbers_it_next_and_leaves_it_empty() -> None:
    anchor = NewAnchor(path="src/app.py", side=Side.NEW, start=2, end=3, base_commit=None)
    excerpt = (ExcerptLine(kind=LineKind.ADDED, text="    return compute()"),)

    after = add_comment(review(comment(4, "x.py", 1, 1, ())), anchor, excerpt, LATER)

    (_, new) = after.comments
    assert (new.number, new.anchor, new.excerpt, new.body, new.created, after.next_number) == (
        5,
        anchor,
        excerpt,
        "",
        LATER,
        6,
    )


def test_edit_comment_changes_only_that_comment() -> None:
    before = review(comment(1, "a.py", 1, 1, (), body="one"), comment(2, "b.py", 1, 1, (), body="two"))

    after = edit_comment(before, 2, lambda body: body + "!")

    assert [c.body for c in after.comments] == ["one", "two!"]


def test_delete_comment_keeps_the_numbering() -> None:
    before = review(comment(1, "a.py", 1, 1, ()), comment(2, "b.py", 1, 1, ()))

    after = delete_comment(before, 1)

    assert ([c.number for c in after.comments], after.next_number) == ([2], 3)


def test_edit_summary() -> None:
    assert edit_summary(review(summary="Looks"), lambda text: text + " good").summary == "Looks good"


def test_review_finds_a_comment_by_number() -> None:
    second = comment(2, "b.py", 1, 1, ())

    assert (review(comment(1, "a.py", 1, 1, ()), second).comment(2), review().comment(2)) == (second, None)


def test_review_start_is_empty_and_numbers_from_one() -> None:
    started = review()

    assert (started.version, started.summary, started.comments, started.next_number, started.base_mode) == (
        1,
        "",
        (),
        1,
        BaseMode.MERGE_BASE,
    )


def test_anchor_for_new_side_spans_the_new_lines_and_keeps_every_selected_line() -> None:
    selected = FILE.lines[1:4]

    anchor, excerpt = anchor_for(FILE, selected, Side.NEW, BASE_COMMIT)

    assert anchor == NewAnchor(path="src/app.py", side=Side.NEW, start=2, end=3, base_commit=None)
    assert excerpt == (
        ExcerptLine(kind=LineKind.REMOVED, text="    return None"),
        ExcerptLine(kind=LineKind.ADDED, text="    return compute()"),
        ExcerptLine(kind=LineKind.ADDED, text="    # computed"),
    )


def test_anchor_for_old_side_records_the_base_commit() -> None:
    anchor, _ = anchor_for(FILE, FILE.lines[1:2], Side.OLD, BASE_COMMIT)

    assert anchor == OldAnchor(path="src/app.py", side=Side.OLD, start=2, end=2, base_commit=BASE_COMMIT)


def test_anchor_for_keeps_the_unified_order_whatever_order_the_lines_come_in() -> None:
    _, excerpt = anchor_for(FILE, (FILE.lines[2], FILE.lines[1]), Side.NEW, BASE_COMMIT)

    assert [line.kind for line in excerpt] == [LineKind.REMOVED, LineKind.ADDED]


def test_anchored_lines_are_on_the_anchors_side_within_its_range() -> None:
    anchor = NewAnchor(path="src/app.py", side=Side.NEW, start=2, end=4, base_commit=None)

    assert anchored_lines(FILE, anchor) == (FILE.lines[2], FILE.lines[3], FILE.lines[4])


def test_comment_is_current_while_its_lines_are_unchanged() -> None:
    current = comment(
        1, "src/app.py", 2, 3, ((LineKind.ADDED, "    return compute()"), (LineKind.ADDED, "    # computed"))
    )

    assert not is_outdated(current, diff_of(FILE))


def test_comment_is_outdated_when_its_lines_changed() -> None:
    stale = comment(1, "src/app.py", 2, 2, ((LineKind.ADDED, "    return other()"),))

    assert is_outdated(stale, diff_of(FILE))


def test_comment_is_outdated_when_its_file_left_the_diff() -> None:
    assert is_outdated(comment(1, "gone.py", 1, 1, ((LineKind.CONTEXT, "x"),)), diff_of(FILE))


def test_old_side_comment_compares_only_removed_lines() -> None:
    current = comment(
        1,
        "src/app.py",
        2,
        4,
        ((LineKind.REMOVED, "    return None"), (LineKind.CONTEXT, ""), (LineKind.REMOVED, "old tail")),
        side=Side.OLD,
    )

    assert not is_outdated(current, diff_of(FILE))


def test_old_side_comment_is_outdated_against_another_base() -> None:
    current = comment(1, "src/app.py", 2, 2, ((LineKind.REMOVED, "    return None"),), side=Side.OLD)
    moved = current.model_copy(update={"anchor": current.anchor.model_copy(update={"base_commit": "f" * 40})})

    assert (is_outdated(current, diff_of(FILE)), is_outdated(moved, diff_of(FILE))) == (False, True)


def test_parse_review_migrates_an_older_review_through_each_version_in_turn() -> None:
    current = review(summary="written, then -1 to 0, then 0 to 1")
    document = json.loads(current.model_dump_json())
    oldest = {**document, "version": -1, "summary": "written"}
    migrations = {
        -1: lambda older: {**older, "version": 0, "summary": f"{older['summary']}, then -1 to 0"},
        0: lambda older: {**older, "version": 1, "summary": f"{older['summary']}, then 0 to 1"},
    }

    assert parse_review(json.dumps(oldest).encode(), migrations) == current


def test_parse_review_reads_the_current_version_as_is() -> None:
    current = review(
        comment(1, "src/app.py", 2, 2, ((LineKind.ADDED, "    return 1"),)),
        comment(2, "src/app.py", 3, 4, ((LineKind.REMOVED, "    pass"),), side=Side.OLD),
        summary="as written",
    )

    assert parse_review(current.model_dump_json().encode()) == current


@pytest.mark.parametrize(("side", "base_commit"), [("old", None), ("new", "abc1234")])
def test_parse_review_refuses_an_anchor_whose_base_commit_does_not_match_its_side(
    side: str, base_commit: str | None
) -> None:
    document = json.loads(review(comment(1, "src/app.py", 2, 2, ((LineKind.ADDED, "x"),))).model_dump_json())
    document["comments"][0]["anchor"].update(side=side, base_commit=base_commit)

    with pytest.raises(ValidationError, match=r"comments\.0\.anchor\.(old|new)\.base_commit"):
        parse_review(json.dumps(document).encode())


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b'{"version": 2}', r"^This review is version 2, and this assess-diff reads up to version 1$"),
        (b'{"version": 0}', r"^This review is version 0, and this assess-diff reads up to version 1$"),
        (b'{"id": "x"}', r"^This review is version None, and this assess-diff reads up to version 1$"),
        (b'{"version": "1"}', r"^This review is version '1', and this assess-diff reads up to version 1$"),
        (b'{"version": 1.0}', r"^This review is version 1\.0, and this assess-diff reads up to version 1$"),
        (b'{"version": true}', r"^This review is version True, and this assess-diff reads up to version 1$"),
        (b'{"version": [1]}', r"^This review is version \[1\], and this assess-diff reads up to version 1$"),
        (b"[1, 2]", r"^A review file holds a JSON object$"),
    ],
)
def test_parse_review_refuses_what_it_cant_read(content: bytes, message: str) -> None:
    with pytest.raises(UnreadableReview, match=message):
        parse_review(content)


def test_default_name_is_the_local_minute_it_started() -> None:
    started = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)

    assert default_name(started) == started.astimezone().strftime("%Y-%m-%d %H:%M")


def test_parse_review_reads_a_comment_on_a_whole_file() -> None:
    current = review(file_comment(1, "src/app.py", body="split this up"))

    assert parse_review(current.model_dump_json().encode()) == current


@pytest.mark.parametrize(("path", "outdated"), [("src/app.py", False), ("src/gone.py", True)], ids=["in", "gone"])
def test_a_comment_on_a_whole_file_is_outdated_only_once_the_file_leaves_the_diff(path: str, outdated: bool) -> None:
    assert is_outdated(file_comment(1, path), diff_of(FILE)) is outdated
