from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import settings
from hypothesis import strategies as st

from assess_diff.diff import Base
from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import FileChange
from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.diff import Hunk
from assess_diff.diff import Line
from assess_diff.diff import LineKind
from assess_diff.diff import SplitRow
from assess_diff.diff import added_file
from assess_diff.diff import build_lines
from assess_diff.diff import header_path
from assess_diff.diff import parse_diff
from assess_diff.diff import placeholder_file
from assess_diff.diff import split_rows
from assess_diff.diff import unquote_path
from assess_diff.diff import untracked_placeholder
from assess_diff.git import compute_diff
from assess_diff.git import find_repository
from assess_diff.git import resolve_base
from assess_diff.review import FOLLOW_HEAD
from tests.conftest import Scratch
from tests.conftest import init_scratch


async def head_diff(scratch: Scratch) -> Diff:
    repository = await find_repository(scratch.root)
    return await compute_diff(repository, await resolve_base(repository, FOLLOW_HEAD, "main"))


def old_content(lines: tuple[Line, ...]) -> list[str]:
    return [line.text for line in lines if line.kind is not LineKind.ADDED]


def new_content(lines: tuple[Line, ...]) -> list[str]:
    return [line.text for line in lines if line.kind is not LineKind.REMOVED]


async def test_modified_file_interleaves_removed_lines_before_their_replacements(scratch: Scratch) -> None:
    scratch.write("app.py", "one\ntwo\nthree\nfour\n")
    scratch.commit()
    scratch.write("app.py", "one\nTWO\nthree\nfour\nfive\n")

    diff = await head_diff(scratch)

    (file,) = diff.files
    assert (file.path, file.status, file.hunk_count) == ("app.py", FileStatus.MODIFIED, 2)
    assert file.lines == (
        Line(LineKind.CONTEXT, 1, 1, "one"),
        Line(LineKind.REMOVED, 2, None, "two", hunk=0),
        Line(LineKind.ADDED, None, 2, "TWO", hunk=0),
        Line(LineKind.CONTEXT, 3, 3, "three"),
        Line(LineKind.CONTEXT, 4, 4, "four"),
        Line(LineKind.ADDED, None, 5, "five", hunk=1),
    )


async def test_removal_without_addition_sits_after_the_line_before_it(scratch: Scratch) -> None:
    scratch.write("app.py", "one\ntwo\nthree\n")
    scratch.commit()
    scratch.write("app.py", "one\nthree\n")

    (file,) = (await head_diff(scratch)).files

    assert file.lines == (
        Line(LineKind.CONTEXT, 1, 1, "one"),
        Line(LineKind.REMOVED, 2, None, "two", hunk=0),
        Line(LineKind.CONTEXT, 3, 2, "three"),
    )


async def test_deleted_file_is_every_line_removed(scratch: Scratch) -> None:
    scratch.write("gone.txt", "a\nb\n")
    scratch.commit()
    (scratch.root / "gone.txt").unlink()

    (file,) = (await head_diff(scratch)).files

    assert file.status is FileStatus.DELETED
    assert file.lines == (Line(LineKind.REMOVED, 1, None, "a", hunk=0), Line(LineKind.REMOVED, 2, None, "b", hunk=0))


async def test_untracked_file_is_every_line_added(scratch: Scratch) -> None:
    scratch.commit()
    scratch.write("new dir/fresh file.py", "x = 1\n")

    (file,) = (await head_diff(scratch)).files

    assert (file.path, file.status) == ("new dir/fresh file.py", FileStatus.UNTRACKED)
    assert file.lines == (Line(LineKind.ADDED, None, 1, "x = 1", hunk=0),)


async def test_staged_new_file_is_added(scratch: Scratch) -> None:
    scratch.commit()
    scratch.write("staged.py", "y = 2\n")
    scratch.git("add", "staged.py")

    (file,) = (await head_diff(scratch)).files

    assert file.status is FileStatus.ADDED
    assert file.additions == 1


async def test_renamed_file_keeps_its_old_path(scratch: Scratch) -> None:
    scratch.write("before.py", "".join(f"line {n}\n" for n in range(20)))
    scratch.commit()
    scratch.git("mv", "before.py", "after.py")
    scratch.write("after.py", "".join(f"line {n}\n" for n in range(19)) + "changed\n")

    (file,) = (await head_diff(scratch)).files

    assert (file.path, file.old_path, file.status) == ("after.py", "before.py", FileStatus.RENAMED)
    assert (file.additions, file.deletions) == (1, 1)


async def test_binary_file_has_a_placeholder_instead_of_lines(scratch: Scratch) -> None:
    scratch.write("image.dat", b"\0\1\2")
    scratch.commit()
    scratch.write("image.dat", b"\0\1\3")

    (file,) = (await head_diff(scratch)).files

    assert (file.path, file.placeholder, file.lines) == ("image.dat", "Binary file", ())


async def test_path_with_a_quote_survives_git_quoting(scratch: Scratch) -> None:
    scratch.write('say "hi".txt', "hello\n")
    scratch.commit()
    scratch.write('say "hi".txt', "goodbye\n")

    (file,) = (await head_diff(scratch)).files

    assert file.path == 'say "hi".txt'


def test_unquote_path_decodes_octal_escapes_as_utf8() -> None:
    assert unquote_path('"caf\\303\\251\\t.txt"') == "café\t.txt"


def test_parse_diff_of_nothing_is_no_files() -> None:
    assert parse_diff("") == ()


def test_split_rows_pair_each_removal_run_with_the_additions_after_it() -> None:
    context = Line(LineKind.CONTEXT, 1, 1, "same")
    removed = (Line(LineKind.REMOVED, 2, None, "a", 0), Line(LineKind.REMOVED, 3, None, "b", 0))
    added = (Line(LineKind.ADDED, None, 2, "A", 0),)

    assert split_rows((context, *removed, *added)) == (
        SplitRow(context, context),
        SplitRow(removed[0], added[0]),
        SplitRow(removed[1], None),
    )


@settings(max_examples=40, deadline=None)
@given(
    old=st.lists(st.sampled_from(["a", "b", "c", "", "d e"]), max_size=12),
    new=st.lists(st.sampled_from(["a", "b", "c", "", "x"]), max_size=12),
)
async def test_rows_rebuild_both_versions_with_consecutive_numbers(old: list[str], new: list[str]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        scratch = init_scratch(Path(directory))
        scratch.write("file.txt", "".join(f"{line}\n" for line in old))
        scratch.commit()
        scratch.write("file.txt", "".join(f"{line}\n" for line in new))

        files = (await head_diff(scratch)).files

    lines = files[0].lines if files else ()
    if old == new:
        assert files == ()
        return
    assert old_content(lines) == old
    assert new_content(lines) == new
    assert [line.old for line in lines if line.old is not None] == list(range(1, len(old) + 1))
    assert [line.new for line in lines if line.new is not None] == list(range(1, len(new) + 1))


@pytest.mark.parametrize("path", ["plain/path.py", '"leading/quote.py', 'trailing/quote.py"'])
def test_unquote_path_leaves_a_path_not_wrapped_in_quotes_alone(path: str) -> None:
    assert unquote_path(path) == path


def test_unquote_path_decodes_named_escapes() -> None:
    assert unquote_path('"a\\\\b\\"c\\nd"') == 'a\\b"c\nd'


@pytest.mark.parametrize(
    ("header", "path"),
    [
        ("diff --git a/img.dat b/img.dat", "img.dat"),
        ("diff --git a/with space.dat b/with space.dat", "with space.dat"),
        ('diff --git "a/qu\\"ote.dat" "b/qu\\"ote.dat"', 'qu"ote.dat'),
        ("diff --git a/x b/y.dat b/x b/y.dat", "x b/y.dat"),
        ('diff --git "a/say\\"" "b/say\\""', 'say"'),
        ('diff --git "a/new\\\\" "b/new\\\\"', "new\\"),
        ('diff --git "a/two\\\\\\"" "b/two\\\\\\""', 'two\\"'),
    ],
)
def test_header_path_reads_the_path_from_the_diff_git_line(header: str, path: str) -> None:
    assert header_path(header) == path


@pytest.mark.parametrize(
    ("lines", "change"),
    [
        pytest.param(
            [
                "diff --git a/before.py b/after.py",
                "similarity index 100%",
                "rename from before.py",
                "rename to after.py",
            ],
            FileChange(path="after.py", old_path="before.py", status=FileStatus.RENAMED, is_binary=False, hunks=()),
            id="pure rename",
        ),
        pytest.param(
            [
                r'diff --git "a/new\\" "b/new\\"',
                "new file mode 100644",
                "index 0000000..92d5444",
                "--- /dev/null",
                r'+++ "b/new\\"',
                "@@ -0,0 +1 @@",
                "+fresh",
            ],
            FileChange(
                path="new\\",
                old_path=None,
                status=FileStatus.ADDED,
                is_binary=False,
                hunks=(Hunk(0, 0, 1, 1, (), ("fresh",)),),
            ),
            id="added",
        ),
        pytest.param(
            [
                r'diff --git "a/old\\" "b/old\\"',
                "deleted file mode 100644",
                "index 286c5f5..0000000",
                r'--- "a/old\\"',
                "+++ /dev/null",
                "@@ -1 +0,0 @@",
                "-gone",
            ],
            FileChange(
                path="old\\",
                old_path=None,
                status=FileStatus.DELETED,
                is_binary=False,
                hunks=(Hunk(1, 1, 0, 0, ("gone",), ()),),
            ),
            id="deleted",
        ),
        pytest.param(
            [
                r'diff --git "a/say\"" "b/say\""',
                "index bdc955b..8835708 100644",
                r'Binary files "a/say\"" and "b/say\"" differ',
            ],
            FileChange(path='say"', old_path=None, status=FileStatus.MODIFIED, is_binary=True, hunks=()),
            id="binary",
        ),
    ],
)
def test_parse_diff_reads_each_file_header(lines: list[str], change: FileChange) -> None:
    assert parse_diff("\n".join(lines) + "\n") == (change,)


def test_parse_diff_reads_a_hunk_header_without_counts_as_one_line() -> None:
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -3 +3 @@\n-old\n+new\n"

    (change,) = parse_diff(text)

    assert change.hunks == (Hunk(3, 1, 3, 1, ("old",), ("new",)),)


def test_parse_diff_skips_the_no_newline_marker() -> None:
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n"

    (change,) = parse_diff(text)

    assert change.hunks[0].removed == ("old",)


def test_parse_diff_rejects_a_line_where_a_hunk_header_belongs() -> None:
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n@@ broken\n"

    with pytest.raises(ValueError, match=r"^Expected a hunk header in a diff, but got '@@ broken'$"):
        parse_diff(text)


@pytest.mark.parametrize(
    "text",
    ["carriage\r", "form\x0cfeed", "line\u2028separator", "paragraph\u2029separator", "next\x85line"],
)
def test_parse_diff_splits_lines_only_on_line_feeds(text: str) -> None:
    diff = f"diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-{text}\n+{text}!\n"

    (change,) = parse_diff(diff)

    assert change.hunks == (Hunk(1, 1, 1, 1, (text,), (f"{text}!",)),)


async def test_a_crlf_files_changed_lines_keep_their_carriage_returns_as_its_context_does(scratch: Scratch) -> None:
    scratch.write("dos.txt", b"one\r\ntwo\r\nthree\r\n")
    scratch.commit()
    scratch.write("dos.txt", b"one\r\nTWO\r\nthree\r\n")

    (file,) = (await head_diff(scratch)).files

    assert [(line.kind, line.text) for line in file.lines] == [
        (LineKind.CONTEXT, "one\r"),
        (LineKind.REMOVED, "two\r"),
        (LineKind.ADDED, "TWO\r"),
        (LineKind.CONTEXT, "three\r"),
    ]


def test_parse_diff_splits_several_files() -> None:
    text = "diff --git a/one b/one\n--- a/one\n+++ b/one\ndiff --git a/two b/two\n--- a/two\n+++ b/two\n"

    assert [change.path for change in parse_diff(text)] == ["one", "two"]


def test_line_number_is_on_its_own_side() -> None:
    assert Line(LineKind.REMOVED, 7, None, "x").number == 7
    assert Line(LineKind.ADDED, None, 9, "x").number == 9
    assert Line(LineKind.CONTEXT, 4, 5, "x").number == 5


def test_line_without_a_number_on_its_side_refuses_to_give_one() -> None:
    with pytest.raises(ValueError, match="has no number on its own side"):
        _ = Line(LineKind.ADDED, 3, None, "x").number


def test_split_row_with_no_lines_has_no_primary() -> None:
    with pytest.raises(ValueError, match="at least one side"):
        _ = SplitRow(None, None).primary


def test_split_row_primary_prefers_the_working_tree() -> None:
    removed = Line(LineKind.REMOVED, 2, None, "a")
    added = Line(LineKind.ADDED, None, 2, "b")

    assert SplitRow(removed, added).primary == added
    assert SplitRow(removed, None).primary == removed


def test_split_rows_start_a_new_pair_when_removals_follow_additions() -> None:
    added = Line(LineKind.ADDED, None, 1, "new", 0)
    removed = Line(LineKind.REMOVED, 1, None, "old", 1)

    assert split_rows((added, removed)) == (SplitRow(None, added), SplitRow(removed, None))


def test_added_file_with_no_lines_has_no_hunks() -> None:
    assert added_file("empty.txt", ()).hunk_count == 0


def test_added_file_is_one_hunk_of_every_line() -> None:
    assert added_file("notes.md", ("alpha", "beta")) == FileDiff(
        path="notes.md",
        old_path=None,
        status=FileStatus.UNTRACKED,
        lines=(Line(LineKind.ADDED, None, 1, "alpha", hunk=0), Line(LineKind.ADDED, None, 2, "beta", hunk=0)),
        hunk_count=1,
    )


def test_placeholder_file_keeps_the_change_but_none_of_its_lines() -> None:
    change = FileChange(
        path="new.bin",
        old_path="old.bin",
        status=FileStatus.RENAMED,
        is_binary=True,
        hunks=(Hunk(4, 2, 4, 3, ("a", "b"), ("c", "d", "e")),),
    )

    assert placeholder_file(change, "Binary file") == FileDiff(
        path="new.bin",
        old_path="old.bin",
        status=FileStatus.RENAMED,
        lines=(),
        hunk_count=0,
        placeholder="Binary file",
    )


def test_untracked_placeholder_is_the_file_with_a_reason_instead_of_lines() -> None:
    assert untracked_placeholder("blob.dat", "File too large to show") == FileDiff(
        path="blob.dat",
        old_path=None,
        status=FileStatus.UNTRACKED,
        lines=(),
        hunk_count=0,
        placeholder="File too large to show",
    )


def test_build_lines_numbers_each_hunk_from_its_header_when_the_file_shrank_after_the_diff() -> None:
    # The working tree can change between `git diff` and reading the file, leaving fewer lines than the hunks expect.
    hunks = (Hunk(old_start=6, old_count=1, new_start=5, new_count=1, removed=("old",), added=("new",)),)

    assert build_lines(hunks, ("a", "b")) == (
        Line(LineKind.CONTEXT, 1, 1, "a"),
        Line(LineKind.CONTEXT, 2, 2, "b"),
        Line(LineKind.REMOVED, 6, None, "old", hunk=0),
        Line(LineKind.ADDED, None, 5, "new", hunk=0),
    )


def test_file_diff_counts_its_additions_and_deletions() -> None:
    lines = (
        Line(LineKind.REMOVED, 1, None, "a", 0),
        Line(LineKind.ADDED, None, 1, "b", 0),
        Line(LineKind.ADDED, None, 2, "c", 0),
        Line(LineKind.CONTEXT, 2, 3, "d"),
    )

    file = FileDiff(path="f", old_path=None, status=FileStatus.MODIFIED, lines=lines, hunk_count=1)

    assert (file.additions, file.deletions) == (2, 1)


def test_base_short_is_the_first_seven_characters() -> None:
    assert Base(BaseMode.HEAD, "0123456789abcdef", "HEAD").short == "0123456"
