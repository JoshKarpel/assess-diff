# assess-diff

Review an agent's working-tree diff in the terminal while it's still working,
and hand the review back to it as Markdown.

`assess-diff` shows every changed file whole, in one scroll, reloading as files change.
You comment on any line or range, write a summary,
and copy the review (`c`) to paste into the agent, comments in the order you wrote them,
each with a `path:line` reference,
and the lines it was about where they've changed since.
Reviews are saved as you type, under `$XDG_DATA_HOME/assess-diff/`,
and belong to the repository, across its worktrees.

![Commenting on a range of lines and a whole file, going to a comment by its number, the split view, choosing a syntax theme, writing the summary, and copying the review](https://raw.githubusercontent.com/JoshKarpel/assess-diff/main/demo/demo.gif)

It's built on [counterweight](https://www.counterweight.dev/).

## Running it

```bash
uv tool install assess-diff
assess-diff              # review this worktree's changes since HEAD (b switches to the merge base)
assess-diff --base main  # review them against the merge base with main
assess-diff --help       # the other commands: list, show, delete
```

The footer lists the keys for whatever you're doing.
`assess-diff show` prints the latest review as Markdown, which an agent can run itself.

Copying uses OSC 52, which most terminals support;
under tmux it needs `set -g set-clipboard on`.

## Developing

See [`CLAUDE.md`](CLAUDE.md) for the commands and conventions,
and [`PLAN.md`](PLAN.md) for the design and the reasoning behind it.
