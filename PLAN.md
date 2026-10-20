# Plan: assess-diff, a terminal diff reviewer for agent work

Artifact: https://claude.ai/code/artifact/f01d128b-a3af-48e7-82b3-591ab856c7f0

This plan covers the first pass of `assess-diff`:
a counterweight TUI that shows a working tree's diff,
reloads it as an agent edits files,
lets you comment on any line or range,
keeps the review on disk as you go,
and exports it as Markdown to paste back into the agent.
Submitting a review to GitHub through `gh` is out of scope for now (see [Later](#later)).

## The question

How do you review an agent's changes _while it's still making them_,
and hand back feedback it can act on,
without a browser, a pushed branch, or the risk of losing half-written comments?

## Goals

- **Live diff.**
  A file watcher on the worktree recomputes the diff whenever files change,
  so the view follows the agent's edits.
- **Two bases, toggleable.**
  Review against `HEAD` (the change since the last commit), which a new review starts on,
  or against the merge base with the default branch (the whole branch, as a PR would show it).
- **Unified and split views**, toggleable.
- **One long scroll** of whole files, each changed file under a separator header.
- **Tab stops on hunks and comments.**
  Tab and Shift+Tab move between them,
  which is how you cross a large file quickly without paging through it.
- **Comment on any line or contiguous range**, in or out of a hunk, or on a whole file.
- **One top-level review comment**, as GitHub's review summary,
  written in a dialog docked over the bottom of the diff, so writing it never moves your place.
- **File picker** on the left, as GitHub's new PR review page has:
  a tree of the changed files, as wide as its widest row, with folding directories,
  a filter that narrows it to the paths matching what's typed,
  and Enter or a click jumps to the file.
- **Syntax highlighting**, in a theme chosen from an autocomplete picker.
- **Mouse and keyboard** for everything.
- **Export to Markdown** with line references, comments in **creation order**,
  so "see my comment above" still reads right after you scroll back up to write it.
  `c` copies it to the clipboard.
- **Reviews persist** as you type, survive a stray Ctrl+C, and can be resumed,
  started fresh, renamed, and deleted, from a dialog in the app or from the CLI.
  Each has a name, the time it started until you rename it.
  Nothing is ever deleted automatically.
- **Reviews belong to projects**, grouped across worktrees of one repository,
  with the current worktree's reviews first.
- **Preferences persist**: the view (unified or split) and the syntax theme
  are the reader's, kept apart from any review.

## Decisions

### Name

The distribution, the console command, and the XDG directories are all `assess-diff`,
and the import package is `assess_diff`.
`assess` itself is taken on PyPI.

### Storage: one JSON file per review

Each review is a pydantic model written to `$XDG_DATA_HOME/assess-diff/reviews/<id>.json`
(`~/.local/share/assess-diff/reviews/` when the variable is unset).
Ids are UUIDv7s, so they sort by when their reviews started,
and every command that takes one accepts any unambiguous prefix, as git does for commits.

Ranked goals: never lose a comment, then simple to operate and inspect,
then cheap listing and filtering.

SQLite's strength is relational queries and transactions across rows.
Neither is needed here.
The project/worktree relationship is two fields on a review,
and grouping or filtering is a pass over a directory of small files,
which stays fast into the thousands of reviews.
A review is only ever written by the one `assess-diff` process that has it open,
so there are no multi-review transactions to protect.

What JSON files buy:

- One file per review means two `assess-diff` processes in two worktrees never contend.
- Each save is a write to a temporary file and an atomic `rename`,
  so a crash mid-save leaves the previous version intact.
- The schema is the pydantic model, declared once, with a `version` field.
  Loading a file an older version wrote migrates it forward, one version at a time, before parsing it;
  a file a newer version wrote, or one that isn't a review, fails with an error naming the file.
  Every change to the model bumps the version and adds the migration from the last.
- A review is a file you can `cat`, back up, or hand to an agent directly.

What it costs:
listing parses every review file;
there's no index to query by project;
and two processes opening the _same_ review would race, last write wins.
The last one is the real risk, so each process remembers a digest of what it last read or wrote,
and a save that finds the file's content different refuses to overwrite it.
Instead it saves this process's version as a new review and says so,
so neither process's comments are dropped.
A content digest rather than a modification time, since two writes within one clock tick share a time.
Each save holds an `flock` on a lock file in the reviews directory across that check and its write,
so two processes saving at once take turns rather than both passing the check.

Every edit, keystrokes in a comment included, is saved before the UI shows it.
The writes are a few kilobytes each,
and saving synchronously means no edit is waiting in a queue when the process exits.

### Project identity: the git common directory

A **project** is the absolute path of the repository's common git directory
(`git rev-parse --path-format=absolute --git-common-dir`),
which every worktree of one repository shares.
A **worktree** is its top level (`git rev-parse --show-toplevel`).
Each review records both, plus the branch it was started on and the base it was last viewed against.

Listing shows a project's reviews with the current worktree's first.
Opening `assess-diff` with no arguments resumes the current worktree's most recently updated review,
or starts one if it has none; a new review isn't written until its first edit.

Moving or re-cloning the repository changes the common directory,
which orphans its reviews from the project grouping (they remain listed under the old path).
A root-commit hash would survive a move,
but would also merge unrelated clones and forks of one repository into one project.
Paths are the simpler identity; revisit if moves turn out to be common.

### The review manager: a dialog in the app

`o` opens a dialog over the app listing **New review** first,
then the project's reviews, this worktree's first and each group most recently updated first,
with each review's name, branch, comment count, and when it was last updated;
the open review is marked, and so are reviews from other worktrees.
Enter starts a new review or opens the highlighted one,
`e` renames it in place (Enter keeps the name, Escape drops the edit),
and `d` pressed twice deletes it, as for comments.
Deleting the open review starts a new one in its place, and leaves the dialog open.
The list is read from disk when the dialog opens and after each rename or delete,
so a review another process saved since shows up the next time.

A review's name defaults to the minute it started, in local time (`2026-10-20 14:03`),
and shows in the status line, in the dialog, and in `assess-diff list`.
The open review can also be renamed where its name shows in the status line, with `e` or a click.

### Preferences: one JSON file under the XDG config directory

The view mode and the syntax theme are a pydantic model
at `$XDG_CONFIG_HOME/assess-diff/preferences.json` (`~/.config/assess-diff/` when unset),
written atomically whenever one changes.
They're configuration rather than data, so they sit apart from the reviews.
A missing file means the defaults (unified, `github-dark`);
a file naming a theme Pygments doesn't have fails to parse, loudly, rather than quietly resetting.

`t` opens the theme picker: a dialog over the app with a field that narrows the themes as you type,
matching the typed letters in order with anything between them, as counterweight's autocomplete recipe does.
The highlighted theme previews on the diff behind the dialog;
Enter keeps it and saves it, Escape reverts.
The app keeps what's typed and highlighted in the picker, and the theme the diff draws with is derived from it,
so the preview changes in the same render as the highlight, with no effect to wait on.

### Computing the diff: git for change regions, the working tree for the rest

The diff is built from two git calls and the working tree:

1. `git diff -U0 --no-color --no-ext-diff --no-textconv -M <base>` lists the changed files,
   their renames, and each change region with the lines it removes and adds.
   `-U0` makes every separate change its own hunk, so each is its own Tab stop,
   and `--no-textconv` keeps the hunks counting the file's own lines, which the rows are laid on.
2. `git ls-files --others --exclude-standard` adds untracked files, as wholly added,
   since agents create new files and plain `git diff` ignores them.
3. Each changed file's content is read from the working tree.

Lines outside every hunk are the same in both versions,
so the working tree's content and the hunks' removed and added lines are the whole of both files:
the base's content never has to be read.
A pure function walks the working tree's lines with the hunks to build the rows of the whole file.
A property test checks that the rows rebuild both versions exactly, against real git.
Binary files, files over 1 MiB, and submodules or nested repositories show a placeholder instead of their lines.

The base is `HEAD`, or `git merge-base HEAD <branch>`,
where the branch is `--base` if given, otherwise what `origin/HEAD` points at,
otherwise a local `main` or `master`.
With none of those, the view stays on `HEAD`, and `b` says to pass `--base`.
A new review starts on `HEAD`, or on the merge base when `--base` names a branch;
a resumed one keeps the base it was last viewed against.

### Live reload: watchfiles, filtered by git

[watchfiles](https://github.com/samuelcolvin/watchfiles)' `awatch` watches the worktree
(and a linked worktree's git directory, which lives outside it)
in an effect on the root component.
Its own filter passes everything in the worktree,
and from the git directory only `HEAD`, the index, and `logs/HEAD`,
which a commit, a checkout, or a reset that moves only a branch ref rewrites.
Each batch of worktree changes then goes through one `git check-ignore --stdin` call,
so every ignore file git reads applies exactly as git applies it,
and a batch that's entirely ignored recomputes nothing.
Any other batch recomputes the diff in the background and sets it as state,
which re-renders only if the result differs.

The watcher still receives events from ignored directories (a `.venv`, `node_modules`) and discards them per batch;
excluding them from the watch itself would mean re-deriving git's ignore rules, which is what this avoids.

### Comment anchors

A comment anchors to a path, a side (`old` or `new`), and a line range on that side,
like GitHub's `start_line`/`line`/`side`.
Added and context lines are commented on the new side,
removed lines on the old side (with the base commit recorded, since the old side depends on it).
A range can't span both sides: it takes the side of the line the selection started on,
and keeps every selected line in its excerpt.

The comment also stores that excerpt of the lines it covers.
Line numbers drift as the agent edits,
so when the lines at the anchor no longer match the excerpt,
the comment is drawn as **outdated** (as GitHub does) at its original position,
and the export says so and quotes the excerpt, so the agent sees what was commented on.
Comments on files that left the diff are drawn after every file.

A comment can also anchor to a whole file, with no side or lines (`side` is `null` in the file),
made with Enter on the file's header and drawn under it.
It's outdated only once its file leaves the diff.
Re-anchoring by searching for the excerpt is [later](#later).

Comments are numbered in creation order, and the number is shown in the UI,
so one comment can refer to another as `#3`.
Numbers are never reused, so deleting one doesn't change what `#3` means.

### Navigation: one cursor, Tab jumps it between stops

The diff view has a single **cursor** and an optional selection anchor.
The cursor is on a position: the summary, a file's header, a row, or a comment.
It moves one position at a time with the arrow keys,
so any line can be selected, not just those in hunks.
Tab and Shift+Tab move it to the next or previous **stop**:
the summary, the first row of a hunk, or a comment.
`n` and `N` do the same for comments alone, skipping the hunks between,
to walk back through what you've written.
Every move scrolls the cursor into view with `ScrollIntoView`.

The cursor is a position value rather than an index,
so when the diff reloads under it, it stays on the same row, or the nearest one left in its file.

Counterweight's focus could make each hunk focusable instead,
but then reaching a line between hunks needs a second cursor
that hands off across component boundaries.
One cursor over all positions, with stops computed by a pure function,
keeps one source of truth and makes navigation testable without the UI.
Counterweight focus is still used where it fits: the file picker, the diff view, the editors, and the theme picker.

In the split view, a row holding a removed line beside an added one points at the added line,
so a comment started there anchors to the new side; the unified view reaches every removed line.

### Rendering

Each file's rows are drawn as a few `Text` elements, one per run of rows drawn alike,
with the cursor's row as an element of its own that carries the scroll target's `ElementHandle`
(the same move `use_text_area` makes for its cursor row).
Runs are also cut at the end of every block of 128 rows,
so a cursor move changes the text of only the block or two it moves within,
and layout measures only those again rather than the whole file on either side of the cursor.
That keeps the element count near the number of blocks, hunks, and comments rather than the number of lines,
which matters because whole files are rendered.
Each run is keyed by the row it starts at, so the node scroll anchoring holds still always shows the same rows,
and a cursor moving down from the top of the view doesn't drag the view along with it.
Each file's view is a `memo` component given the cursor and selection only when they're in that file,
with the app's handlers behind one `Actions` that stays the same object across renders,
so moving the cursor re-renders one or two files, and each row's chunks are cached by value.
The document of positions is rebuilt only when the diff, the view, or a comment's number or anchor changes,
not as a comment's text is typed.
A click maps to a row through the receiver's relative position.

`just bench` times how long each kind of key takes to answer on generated diffs of 2,000 to 40,000 lines,
and `just profile` runs one of those workloads under the sampling profiler.

Split view pairs each hunk's removed and added lines side by side, as GitHub does,
with context lines on both sides, and cuts lines too long for their half with `…`.

Syntax colors come from counterweight's `highlight` (Pygments), with the lexer chosen by file name.
The working tree's lines are highlighted as one document, so constructs spanning lines color right;
each hunk's removed lines are highlighted together, since the base's whole file is never read.
The diff's own backgrounds win over a theme's, so added and removed lines stay distinct in every theme.

### Export format

````markdown
# Review of `my-branch` against `main` (merge base `abc1234`)

The top-level comment.

## 1. `src/app.py:42-45`

The comment body.

## 2. `src/old.py`, removed lines 10-12 (in `abc1234`)

...

## 3. `src/handler.py:7`

_These lines have changed since this comment was written; the excerpt shows them as they were._

```diff
-    return None
+    return compute()
```

The comment body.

## 4. `src/views.py`

A comment on the whole file.
````

New-side ranges use `path:line` or `path:start-end`, which agents read as file references.
Old-side ranges say they're removed lines and name the base commit,
and a comment on a whole file names just its path.
The agent can read the lines a reference points at, so a comment quotes its excerpt only once it's outdated,
when the working tree no longer has those lines anywhere:
as a `diff` block, fenced with more backticks than any run inside it.

`c` copies it with counterweight's `CopyToClipboard` (OSC 52),
which works where the terminal supports it, and tmux passes it on only with `set -g set-clipboard on`.
`assess-diff show` prints the same Markdown, which an agent can also run itself.

### Keys

| Key                         | Action                                                          |
|-----------------------------|-----------------------------------------------------------------|
| Up, Down, `k`, `j`          | Move the cursor                                                 |
| Shift+Up, Shift+Down, `K`, `J` | Extend the selection                                         |
| PageUp, PageDown            | Scroll seven eighths of the view, as a text area pages, moving the cursor as far |
| Home, End                   | Move the cursor to the summary or the last line                 |
| Tab, Shift+Tab              | Next or previous hunk or comment                                |
| `n`, `N`                    | Next or previous comment                                        |
| Enter                       | Comment on the selection or, on a header, the whole file; or edit the comment under the cursor |
| Escape                      | Finish editing (an empty comment is discarded), or clear the selection |
| `d`, `d`                    | Delete the comment under the cursor (pressed twice, to confirm) |
| `r`                         | Write the review summary, in a dialog that keeps your place     |
| `e`                         | Rename the review in the status line (Enter keeps the new name, Escape the old) |
| `s`                         | Toggle unified and split views                                  |
| `b`                         | Toggle the base between `HEAD` and the merge base               |
| `t`                         | Choose the syntax theme                                         |
| `f`                         | Focus the file picker, unfolding it if it's folded away         |
| `F`                         | Fold the file picker away, or back                              |
| `?`                         | Fold the diff's key hints away, or back                         |
| `o`                         | Open the review manager                                         |
| `c`                         | Copy the review as Markdown                                     |
| `q`                         | Quit; the review is already saved                               |

In the file picker, Enter jumps to a file, or folds or unfolds a directory, as Left and Right do,
and `/` narrows it to the files whose paths match what's typed, as the theme picker narrows themes:
Enter jumps to the highlighted match, and Escape goes back to the tree.

Tab, Shift+Tab, or an Escape nothing else takes, pressed in a dialog or an editor, closes it and returns to the diff,
as Escape does in each, so nothing is left open without focus.

The status line and the key hints are drawn as pills, as zellij's status bar is,
and wrap onto more rows between pills rather than running off a narrow screen.

Clicking a line moves the cursor there, Shift+click extends the selection,
clicking a comment edits it, clicking the summary opens its dialog, clicking the review's name renames it,
and clicking a file in the picker jumps to it.
The wheel scrolls either pane.

### CLI

- `assess-diff`: open the TUI on the current worktree's latest review, or a new one.
- `assess-diff --new`: start a new review.
- `assess-diff --review <id>`: open a specific review.
- `assess-diff --base <branch>`: take the merge base with `<branch>` rather than the default branch,
  and start a new review on it.
- `assess-diff list`: the project's reviews, the current worktree's first, with their names.
- `assess-diff show [<id>]`: print a review as Markdown (the latest for this worktree by default).
- `assess-diff delete <id>`: delete a review.

`assess-diff` alone means `assess-diff review`.
`--data-home` and `--config-home` default to `$XDG_DATA_HOME` and `$XDG_CONFIG_HOME`.
The CLI uses [without-cli](https://without.help/), as counterweight does.

## Steps

Every step lands with tests that hold line and branch coverage at 100%,
and mutation testing (mutmut) runs over it, aiming for zero non-equivalent survivors,
with each equivalent survivor written down beside why no test can catch it.

1. **Skeleton.** `pyproject.toml` (counterweight from git, pydantic, pygments, watchfiles, without-cli),
   `mise.toml` and `mise.lock`, `justfile`, ruff, mypy, pytest with the coverage gate, mutmut, pre-commit,
   and GitHub Actions as counterweight has them: a quality check on Linux and macOS
   (pre-commit, build, mypy, pytest with coverage), trusted publishing to PyPI on a release,
   dependabot with cooldowns, and zizmor requiring hash-pinned actions.
2. **Diff core.** Parse `git diff -U0` output and build each file's rows, unified and split.
3. **Git shell.** Resolve bases, run the git calls, read the working tree, filter watched changes through git.
   Tests against a temporary repository.
4. **Review model and storage.** Pydantic models, XDG paths, atomic saves, the content-digest guard and fork,
   listing by project and worktree. Tests on a temporary directory.
5. **Preferences.** The model, its file, and theme validation.
6. **Export.** Markdown from a review and the current diff.
7. **Navigation.** The document of positions, stops, cursor moves, selection, and recovery across reloads.
8. **TUI.** Layout, file picker, diff view with syntax colors, comment editing, the summary,
   view and base toggles, the theme picker, the review manager, copying.
   Tests drive it headless with counterweight's autopilot.
9. **CLI.** The subcommands above.
10. **Mutation testing** over everything, with equivalent survivors recorded in the `mutation-testing` project skill.

## Later

- Submit the review to GitHub through `gh`, as a PR review with line comments.
- Re-anchor outdated comments by searching for their excerpt.
- Comments on files outside the diff.
- Rendering only the rows in view, if whole files prove slow.
- Highlight the file in view in the picker as you scroll.
