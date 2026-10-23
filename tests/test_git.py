from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from watchfiles import Change

from assess_diff.diff import Base
from assess_diff.diff import BaseMode
from assess_diff.diff import FileStatus
from assess_diff.diff import LineKind
from assess_diff.git import MAX_FILE_BYTES
from assess_diff.git import GitError
from assess_diff.git import changes_matter
from assess_diff.git import compute_diff
from assess_diff.git import default_branch
from assess_diff.git import find_repository
from assess_diff.git import git
from assess_diff.git import ignored
from assess_diff.git import is_watched
from assess_diff.git import read_lines
from assess_diff.git import relevant_batches
from assess_diff.git import resolve_base
from tests.conftest import Scratch
from tests.conftest import init_scratch


async def test_find_repository_of_a_main_worktree(scratch: Scratch) -> None:
    scratch.commit()

    repository = await find_repository(scratch.root / ".")

    assert (repository.worktree, repository.git_dir, repository.common_dir, repository.branch) == (
        scratch.root,
        scratch.root / ".git",
        scratch.root / ".git",
        "main",
    )


async def test_find_repository_of_a_linked_worktree_shares_the_common_dir(scratch: Scratch, tmp_path: Path) -> None:
    scratch.commit()
    linked = tmp_path / "linked"
    scratch.git("worktree", "add", "--quiet", "-b", "feature", str(linked))

    repository = await find_repository(linked)

    assert (repository.worktree, repository.git_dir, repository.common_dir, repository.branch) == (
        linked,
        scratch.root / ".git" / "worktrees" / "linked",
        scratch.root / ".git",
        "feature",
    )


async def test_find_repository_with_a_detached_head_has_no_branch(scratch: Scratch) -> None:
    commit = scratch.commit()
    scratch.git("checkout", "--quiet", "--detach", commit)

    assert (await find_repository(scratch.root)).branch is None


async def test_git_failure_names_the_command_and_what_git_said(scratch: Scratch) -> None:
    with pytest.raises(GitError, match=r"^git rev-parse --verify nonsense failed: fatal: "):
        await git(scratch.root, "rev-parse", "--verify", "nonsense")


async def test_git_failure_replaces_bytes_in_stderr_that_are_not_utf8(scratch: Scratch) -> None:
    scratch.commit()

    with pytest.raises(GitError, match=r"^git show no\udcffpe failed: fatal: ambiguous argument 'no\ufffdpe': unknown"):
        await git(scratch.root, "show", os.fsdecode(b"no\xffpe"))


async def test_git_without_stdin_gives_git_an_empty_one(scratch: Scratch) -> None:
    # The empty blob's id, which every SHA-1 repository shares.
    assert await git(scratch.root, "hash-object", "--stdin") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\n"


async def test_default_branch_follows_origin_head(scratch: Scratch, tmp_path: Path) -> None:
    scratch.commit()
    scratch.git("branch", "trunk")
    clone = init_scratch(tmp_path / "clone")
    clone.git("remote", "add", "origin", str(scratch.root))
    clone.git("fetch", "--quiet", "origin")
    clone.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    assert await default_branch(await find_repository(clone.root)) == "origin/trunk"


@pytest.mark.parametrize("name", ["main", "master"])
async def test_default_branch_without_a_remote_is_a_local_main_or_master(tmp_path: Path, name: str) -> None:
    scratch = init_scratch(tmp_path / "repo")
    scratch.git("checkout", "--quiet", "-b", name)
    scratch.commit()
    scratch.git("checkout", "--quiet", "-b", "feature")

    assert await default_branch(await find_repository(scratch.root)) == name


async def test_default_branch_with_nothing_to_go_on_asks_for_one(tmp_path: Path) -> None:
    scratch = init_scratch(tmp_path / "repo")
    scratch.git("checkout", "--quiet", "-b", "feature")
    scratch.commit()

    with pytest.raises(GitError, match=r"^Can't tell which branch this one merges into; pass it with --base$"):
        await default_branch(await find_repository(scratch.root))


async def test_default_branch_is_not_a_file_named_like_a_branch_ref(tmp_path: Path) -> None:
    scratch = init_scratch(tmp_path / "repo")
    scratch.git("checkout", "--quiet", "-b", "feature")
    scratch.write("refs/heads/main", "not a branch\n")
    scratch.commit()

    with pytest.raises(GitError, match="pass it with --base"):
        await default_branch(await find_repository(scratch.root))


async def test_head_base_is_the_last_commit(scratch: Scratch) -> None:
    scratch.commit("first")
    last = scratch.commit("last")
    repository = await find_repository(scratch.root)

    assert await resolve_base(repository, BaseMode.HEAD, "trunk") == Base(mode=BaseMode.HEAD, commit=last, label="HEAD")


async def test_merge_base_is_where_the_branch_left_its_target(scratch: Scratch) -> None:
    fork = scratch.commit("fork")
    scratch.git("checkout", "--quiet", "-b", "feature")
    scratch.write("feature.txt", "work\n")
    scratch.commit("work")
    scratch.git("checkout", "--quiet", "main")
    scratch.write("main.txt", "more\n")
    scratch.commit("more")
    scratch.git("checkout", "--quiet", "feature")
    repository = await find_repository(scratch.root)

    base = await resolve_base(repository, BaseMode.MERGE_BASE, "main")

    assert (base.mode, base.commit, base.label) == (BaseMode.MERGE_BASE, fork, "main")


async def test_merge_base_diff_includes_the_branchs_commits(scratch: Scratch) -> None:
    scratch.commit("fork")
    scratch.git("checkout", "--quiet", "-b", "feature")
    scratch.write("committed.txt", "work\n")
    scratch.commit("work")
    repository = await find_repository(scratch.root)

    head = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))
    branch = await compute_diff(repository, await resolve_base(repository, BaseMode.MERGE_BASE, "main"))

    assert ([file.path for file in head.files], [file.path for file in branch.files]) == ([], ["committed.txt"])


async def test_compute_diff_sorts_files_by_path(scratch: Scratch) -> None:
    scratch.commit()
    scratch.write("b.txt", "b\n")
    scratch.write("a.txt", "a\n")
    repository = await find_repository(scratch.root)

    diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))

    assert [file.path for file in diff.files] == ["a.txt", "b.txt"]


async def test_untracked_binary_file_has_a_placeholder(scratch: Scratch) -> None:
    scratch.commit()
    scratch.write("blob.dat", b"\0\1")
    repository = await find_repository(scratch.root)

    (file,) = (await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))).files

    assert (file.path, file.status, file.placeholder) == ("blob.dat", FileStatus.UNTRACKED, "Binary file")


async def test_modified_file_too_large_to_show_has_a_placeholder(scratch: Scratch) -> None:
    scratch.write("big.txt", "small\n")
    scratch.commit()
    scratch.write("big.txt", "x" * (MAX_FILE_BYTES + 1))
    repository = await find_repository(scratch.root)

    (file,) = (await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))).files

    assert (file.status, file.placeholder) == (FileStatus.MODIFIED, "File too large to show")


def test_read_lines_splits_on_line_feeds_alone(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"one\r\ntwo\x0bstill two\nthree")

    assert read_lines(path) == ("one\r", "two\x0bstill two", "three")


def test_read_lines_of_a_file_at_the_size_limit_reads_it(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x" * (MAX_FILE_BYTES - 1) + b"\n")

    assert read_lines(path) == ("x" * (MAX_FILE_BYTES - 1),)


def test_read_lines_of_a_symlink_is_its_target(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to("somewhere/else")

    assert read_lines(tmp_path / "link") == ("somewhere/else",)


def test_read_lines_of_a_file_removed_since_the_diff_says_so(tmp_path: Path) -> None:
    assert read_lines(tmp_path / "gone") == "File removed while reading"


def test_read_lines_of_a_directory_says_it_is_a_repository(tmp_path: Path) -> None:
    assert read_lines(tmp_path) == "Submodule or nested repository"


def add_submodule(scratch: Scratch, tmp_path: Path) -> Scratch:
    """`scratch` with a submodule at `sub`, committed, and the submodule's own checkout to change it in."""
    inner = init_scratch(tmp_path / "inner")
    inner.commit()
    scratch.git("-c", "protocol.file.allow=always", "submodule", "--quiet", "add", str(inner.root), "sub")
    scratch.commit()
    return init_scratch(scratch.root / "sub")


async def test_a_moved_submodule_has_a_placeholder(scratch: Scratch, tmp_path: Path) -> None:
    add_submodule(scratch, tmp_path).commit("moved")
    repository = await find_repository(scratch.root)

    (file,) = (await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))).files

    assert (file.path, file.status, file.placeholder) == ("sub", FileStatus.MODIFIED, "Submodule or nested repository")


async def test_an_untracked_nested_repository_has_a_placeholder(scratch: Scratch) -> None:
    scratch.commit()
    init_scratch(scratch.root / "nested").commit()
    repository = await find_repository(scratch.root)

    (file,) = (await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))).files

    assert (file.path, file.status, file.placeholder) == (
        "nested/",
        FileStatus.UNTRACKED,
        "Submodule or nested repository",
    )


async def test_diff_rows_ignore_a_textconv_driver(scratch: Scratch) -> None:
    scratch.write(".gitattributes", "*.txt diff=headless\n")
    scratch.git("config", "diff.headless.textconv", "sed 1d")
    scratch.write("f.txt", "keep\nsame\n")
    scratch.commit()
    scratch.write("f.txt", "keep\nnew\n")
    repository = await find_repository(scratch.root)

    (file,) = (await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))).files

    assert [(line.kind, line.text) for line in file.lines] == [
        (LineKind.CONTEXT, "keep"),
        (LineKind.REMOVED, "same"),
        (LineKind.ADDED, "new"),
    ]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("color.diff", "always"),
        ("diff.external", "echo external"),
        ("diff.renames", "false"),
        ("diff.noprefix", "true"),
    ],
)
async def test_compute_diff_is_unaffected_by_the_repositorys_diff_config(
    scratch: Scratch, key: str, value: str
) -> None:
    scratch.write("gone.txt", "gone\n")
    scratch.write("kept.txt", "same\nold\n")
    scratch.write("old.txt", "moved\nintact\n")
    scratch.commit()
    scratch.git("config", key, value)
    (scratch.root / "gone.txt").unlink()
    scratch.write("kept.txt", "same\nnew\n")
    scratch.git("mv", "old.txt", "new.txt")
    repository = await find_repository(scratch.root)

    diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))

    assert [
        (file.path, file.old_path, file.status, [(line.kind, line.text) for line in file.lines]) for file in diff.files
    ] == [
        ("gone.txt", None, FileStatus.DELETED, [(LineKind.REMOVED, "gone")]),
        (
            "kept.txt",
            None,
            FileStatus.MODIFIED,
            [(LineKind.CONTEXT, "same"), (LineKind.REMOVED, "old"), (LineKind.ADDED, "new")],
        ),
        ("new.txt", "old.txt", FileStatus.RENAMED, [(LineKind.CONTEXT, "moved"), (LineKind.CONTEXT, "intact")]),
    ]


async def test_compute_diff_carries_on_past_binary_and_deleted_files(scratch: Scratch) -> None:
    scratch.write("a.dat", b"\0before")
    scratch.write("b.txt", "deleted\n")
    scratch.write("c.txt", "before\n")
    scratch.commit()
    scratch.write("a.dat", b"\0after")
    (scratch.root / "b.txt").unlink()
    scratch.write("c.txt", "after\n")
    scratch.write("d.txt", "untracked\n")
    repository = await find_repository(scratch.root)

    diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))

    assert [(file.path, file.status, file.placeholder, file.additions, file.deletions) for file in diff.files] == [
        ("a.dat", FileStatus.MODIFIED, "Binary file", 0, 0),
        ("b.txt", FileStatus.DELETED, None, 0, 1),
        ("c.txt", FileStatus.MODIFIED, None, 1, 1),
        ("d.txt", FileStatus.UNTRACKED, None, 1, 0),
    ]


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS refuses a filename that isn't UTF-8")
async def test_compute_diff_keeps_a_path_that_is_not_utf8(scratch: Scratch) -> None:  # pragma: darwin
    path = os.fsdecode(b"caf\xe9.txt")
    scratch.write(path, "before\n")
    scratch.commit()
    scratch.write(path, "after\n")
    repository = await find_repository(scratch.root)

    (file,) = (await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))).files

    assert (file.path, [(line.kind, line.text) for line in file.lines]) == (
        path,
        [(LineKind.REMOVED, "before"), (LineKind.ADDED, "after")],
    )


def test_read_lines_of_invalid_utf8_is_binary(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"\xff\xfe")

    assert read_lines(tmp_path / "f") == "Binary file"


@pytest.mark.parametrize(
    ("path", "watched"),
    [
        ("/repo/.git/HEAD", True),
        ("/repo/.git/index", True),
        ("/repo/.git/objects/ab/cdef", False),
        ("/repo/.git/refs/heads/HEAD", False),
        ("/repo/.git/ORIG_HEAD", False),
        ("/repo/.git/logs/HEAD", True),
        ("/repo/.git/logs/refs/heads/main", False),
        ("/repo/.git/logs/index", False),
        ("/repo/src/app.py", True),
        ("/repo/node_modules/x.js", True),
    ],
)
def test_is_watched_passes_the_worktree_and_the_git_state_files(path: str, watched: bool) -> None:
    assert is_watched(Path("/repo/.git"), Path(path)) is watched


async def test_ignored_asks_git_which_paths_it_ignores(scratch: Scratch) -> None:
    scratch.write(".gitignore", "*.log\nbuild/\n")
    scratch.write("tracked.log", "kept anyway\n")
    scratch.git("add", "--force", "tracked.log")
    scratch.commit()
    repository = await find_repository(scratch.root)
    paths = frozenset(scratch.root / name for name in ("debug.log", "build/out.o", "src/app.py", "tracked.log"))

    assert await ignored(repository, paths) == {scratch.root / "debug.log", scratch.root / "build/out.o"}


async def test_ignored_when_git_ignores_none_of_the_paths_is_empty(scratch: Scratch) -> None:
    scratch.write(".gitignore", "*.log\n")
    scratch.commit()
    repository = await find_repository(scratch.root)

    assert await ignored(repository, frozenset({scratch.root / "app.py", scratch.root / "notes.txt"})) == frozenset()


async def test_ignored_passes_a_path_that_is_not_utf8_through_git(scratch: Scratch) -> None:
    scratch.write(".gitignore", "*.log\n")
    scratch.commit()
    repository = await find_repository(scratch.root)
    path = scratch.root / os.fsdecode(b"caf\xe9.log")

    assert await ignored(repository, frozenset({path, scratch.root / "app.py"})) == {path}


async def test_ignored_of_no_paths_is_none(scratch: Scratch) -> None:
    scratch.commit()

    assert await ignored(await find_repository(scratch.root), frozenset()) == frozenset()


async def test_changes_matter_unless_every_path_is_ignored(scratch: Scratch) -> None:
    scratch.write(".gitignore", "*.log\n")
    scratch.commit()
    repository = await find_repository(scratch.root)

    assert not await changes_matter(repository, frozenset({scratch.root / "a.log", scratch.root / "b.log"}))
    assert await changes_matter(repository, frozenset({scratch.root / "a.log", scratch.root / "app.py"}))


async def test_changes_inside_a_submodule_matter(scratch: Scratch, tmp_path: Path) -> None:
    add_submodule(scratch, tmp_path)
    repository = await find_repository(scratch.root)

    assert await changes_matter(repository, frozenset({scratch.root / "sub" / "new.txt"}))


async def test_changes_to_the_git_state_always_matter(scratch: Scratch) -> None:
    scratch.commit()
    repository = await find_repository(scratch.root)

    assert await changes_matter(repository, frozenset({repository.git_dir / "HEAD"}))


async def test_untracked_files_respect_every_ignore_file(scratch: Scratch) -> None:
    scratch.write(".gitignore", "*.tmp\n")
    scratch.write("sub/.gitignore", "local.txt\n")
    scratch.commit()
    (scratch.root / ".git" / "info").mkdir(exist_ok=True)
    (scratch.root / ".git" / "info" / "exclude").write_text("secret.txt\n")
    for name in ("a.tmp", "sub/local.txt", "secret.txt", "kept.txt"):
        scratch.write(name, "x\n")
    repository = await find_repository(scratch.root)

    diff = await compute_diff(repository, await resolve_base(repository, BaseMode.HEAD, "main"))

    assert [file.path for file in diff.files] == ["kept.txt"]


def test_scratch_runs_git_without_the_developers_global_config() -> None:
    assert os.environ["GIT_CONFIG_GLOBAL"] == os.devnull


async def batches_of(*batches: set[tuple[Change, str]]) -> AsyncIterator[set[tuple[Change, str]]]:
    for batch in batches:
        yield batch


async def test_relevant_batches_skip_batches_git_ignores_entirely(scratch: Scratch) -> None:
    scratch.write(".gitignore", "*.log\n")
    scratch.commit()
    repository = await find_repository(scratch.root)
    ignored_only = {(Change.modified, str(scratch.root / "debug.log"))}
    mixed = {(Change.added, str(scratch.root / "debug.log")), (Change.added, str(scratch.root / "app.py"))}

    found = [item async for item in relevant_batches(repository, batches_of(ignored_only, mixed, ignored_only))]

    assert found == [None]
