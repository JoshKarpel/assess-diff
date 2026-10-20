"""The git shell: runs git in a worktree and reads the working tree, then builds the diff with `assess_diff.diff`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from watchfiles import Change

from assess_diff.diff import Base
from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import FileChange
from assess_diff.diff import FileDiff
from assess_diff.diff import FileStatus
from assess_diff.diff import added_file
from assess_diff.diff import file_diff
from assess_diff.diff import parse_diff
from assess_diff.diff import placeholder_file
from assess_diff.diff import untracked_placeholder

# Past this, a file's content isn't shown: an agent's stray log or build output would swamp the view.
MAX_FILE_BYTES = 1024 * 1024


class GitError(Exception):
    """A git command failed; the message carries its arguments and what it wrote to stderr."""


@dataclass(frozen=True, slots=True)
class Repository:
    """
    A worktree, and the repository it belongs to.
    `common_dir` is the git directory every worktree of the repository shares, which is what makes reviews
    from several worktrees one project.
    """

    worktree: Path
    git_dir: Path
    """This worktree's own git directory, which holds its `HEAD` and index."""
    common_dir: Path
    branch: str | None
    """The checked-out branch, or `None` with a detached `HEAD`."""


# The files in a worktree's git directory whose changes can change the diff: a checkout rewrites `HEAD` and the index,
# and every move of `HEAD` appends to its reflog, including a commit or `reset --soft` that writes only a branch ref
# (in the common directory, for a linked worktree) and leaves `HEAD` and the index alone.
# Everything else there (objects, other logs, locks) churns without changing the diff.
GIT_STATE_FILES = frozenset({Path("HEAD"), Path("index"), Path("logs/HEAD")})


def is_watched(git_dir: Path, path: Path) -> bool:
    """
    Whether the watcher passes on a change to `path`: anything outside the git directory,
    and the git directory's `HEAD`, index and `HEAD` reflog. Ignored files are filtered afterwards, by git itself.
    """
    if path.is_relative_to(git_dir):
        return path.relative_to(git_dir) in GIT_STATE_FILES
    return True


async def git(worktree: Path, *args: str, stdin: str = "", ok: frozenset[int] = frozenset({0})) -> str:
    """Git's output, run in `worktree`, given `stdin`, failing unless it exits with one of the `ok` codes."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(worktree),
        "-c",
        "core.quotePath=false",
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(stdin.encode(errors="surrogateescape"))
    if process.returncode not in ok:
        raise GitError(f"git {' '.join(args)} failed: {stderr.decode(errors='replace').strip()}")
    return stdout.decode(errors="surrogateescape")


async def ignored(repository: Repository, paths: frozenset[Path]) -> frozenset[Path]:
    """
    Which of `paths`, all in the worktree, git ignores, by every ignore file it reads.
    A tracked file is never ignored, whatever patterns it matches, since its changes show in the diff.
    """
    if not paths:
        return frozenset()
    output = await git(
        repository.worktree,
        "check-ignore",
        "--stdin",
        "-z",
        stdin="".join(f"{path}\0" for path in paths),
        # 1 means none of them is ignored.
        ok=frozenset({0, 1}),
    )
    return frozenset(Path(path) for path in output.split("\0") if path)


async def relevant_batches(
    repository: Repository, batches: AsyncIterator[set[tuple[Change, str]]]
) -> AsyncIterator[None]:
    """One item for each of the watcher's `batches` of changes that can change the diff, skipping the rest."""
    async for changes in batches:
        if await changes_matter(repository, frozenset(Path(path) for _, path in changes)):
            yield None


async def changes_matter(repository: Repository, paths: frozenset[Path]) -> bool:
    """Whether a batch of changed paths can change the diff: one moved the git state, or one isn't ignored."""
    in_worktree = frozenset(path for path in paths if not path.is_relative_to(repository.git_dir))
    if in_worktree != paths:
        return True
    try:
        return bool(in_worktree - await ignored(repository, in_worktree))
    except GitError:
        # `check-ignore` refuses a path inside a submodule, and a change there can dirty the submodule in the diff.
        return True


async def find_repository(path: Path) -> Repository:
    worktree, git_dir, common_dir = (
        await git(
            path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"
        )
    ).splitlines()
    branch = (await git(path, "branch", "--show-current")).strip()
    return Repository(
        worktree=Path(worktree),
        git_dir=Path(git_dir),
        common_dir=Path(common_dir),
        branch=branch or None,
    )


async def default_branch(repository: Repository) -> str:
    """
    The branch the merge base is taken with: what `origin/HEAD` points at, or else a local `main` or `master`.

    Raises:
        GitError: If the repository has none of those, so the caller has to name one.
    """
    try:
        return (await git(repository.worktree, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")).strip()
    except GitError:
        pass
    for name in ("main", "master"):
        try:
            await git(repository.worktree, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
        except GitError:
            continue
        return name
    raise GitError("Can't tell which branch this one merges into; pass it with --base")


async def resolve_base(repository: Repository, mode: BaseMode, target: str) -> Base:
    """The commit to compare the working tree with, for `mode`, where `target` is the default branch."""
    match mode:
        case BaseMode.HEAD:
            commit = (await git(repository.worktree, "rev-parse", "HEAD")).strip()
            return Base(mode=mode, commit=commit, label="HEAD")
        case BaseMode.MERGE_BASE:
            commit = (await git(repository.worktree, "merge-base", "HEAD", target)).strip()
            return Base(mode=mode, commit=commit, label=target)
        case _ as unreachable:
            assert_never(unreachable)


async def compute_diff(repository: Repository, base: Base) -> Diff:
    """The working tree against `base`, tracked changes and untracked files both, with every file whole."""
    diff_text, untracked_text = await asyncio.gather(
        git(
            repository.worktree,
            "diff",
            "-U0",
            "--no-color",
            "--no-ext-diff",
            # A textconv driver's hunks would count lines in its output, not in the working-tree file they're laid on.
            "--no-textconv",
            "-M",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            base.commit,
        ),
        git(repository.worktree, "ls-files", "--others", "--exclude-standard", "-z"),
    )
    changes = parse_diff(diff_text)
    untracked = tuple(path for path in untracked_text.split("\0") if path)
    files = await asyncio.to_thread(read_files, repository.worktree, changes, untracked)
    return Diff(base=base, files=tuple(sorted(files, key=lambda file: file.path)))


def read_files(worktree: Path, changes: tuple[FileChange, ...], untracked: tuple[str, ...]) -> list[FileDiff]:
    """Each change and untracked file with its working-tree content; blocking, so it runs in a thread."""
    files: list[FileDiff] = []
    for change in changes:
        if change.is_binary:
            files.append(placeholder_file(change, "Binary file"))
            continue
        if change.status is FileStatus.DELETED:
            files.append(file_diff(change, ()))
            continue
        match read_lines(worktree / change.path):
            case str() as reason:
                files.append(placeholder_file(change, reason))
            case lines:
                files.append(file_diff(change, lines))
    for path in untracked:
        match read_lines(worktree / path):
            case str() as reason:
                files.append(untracked_placeholder(path, reason))
            case lines:
                files.append(added_file(path, lines))
    return files


def read_lines(path: Path) -> tuple[str, ...] | str:
    """
    A working-tree file's lines, split as git splits them (on line feeds alone), or why they can't be shown.
    A symbolic link's content is its target, as git records it.
    """
    if path.is_symlink():
        return (str(path.readlink()),)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return "File too large to show"
        content = path.read_bytes()
    except FileNotFoundError:
        # The file went away between `git diff` and this read; the watcher brings the next diff.
        return "File removed while reading"
    except IsADirectoryError:
        # A submodule, or an untracked repository nested in the worktree, which `ls-files` lists as `path/`.
        return "Submodule or nested repository"
    if b"\0" in content:
        return "Binary file"
    try:
        text = content.decode()
    except UnicodeDecodeError:
        return "Binary file"
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return tuple(lines)
