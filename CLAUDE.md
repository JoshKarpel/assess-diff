# CLAUDE.md

## Project Overview

`assess-diff` is a terminal UI for reviewing a working tree's diff while an agent is still changing it,
and handing the review back to the agent as Markdown.
It's built on [counterweight](https://github.com/JoshKarpel/counterweight) (React-like TUI framework).
`README.md` describes what it does for the person using it.

When counterweight lacks something assess-diff needs, the fix belongs upstream in counterweight,
not as a workaround here.
The counterweight checkout at `~/projects/counterweight` has its docs in `docs/` and its API in `src/`.

## Layout

- `src/assess_diff/` is split functional core / imperative shell:
  pure modules (diff parsing and row building, navigation, Markdown export, the review model)
  take values and return values;
  the shell (git subprocesses, the file watcher, review storage, the CLI) does the I/O and feeds them.
- Components live in `src/assess_diff/ui/`. They hold UI state; anything worth a unit test goes in a pure function beside them.
- The reasoning behind a design lives in the docstring of the module, class, or function it shapes,
  not in a separate design doc.

## Development

```bash
mise install   # just, uv
uv sync
just test      # mypy, then pytest with the coverage gate
just mutate    # mutmut, or `just mutate 'assess_diff.diff.*'` to re-run some mutants
just bench     # per-key latency on generated diffs, or `just bench --only large/down` for one workload
just bench-layout  # time to lay out those diffs by view, scope, and comments per file
just profile large/down  # that workload under the sampling profiler, with a flamegraph in profiles/
just demo      # record the README's demo as demo/demo.cast, drawing demo/demo.gif with agg if it changed
just pre-commit
```

A performance claim needs `just bench` (or, for laying out the diff, `just bench-layout`) numbers
from before and after the change, from the same machine.
The benchmarks in `benchmarks/` sit outside the coverage gate, and reuse the tests' harness.

A pre-commit hook records the demo again whenever `src/assess_diff/`, `demo/`, or `uv.lock` changes,
so a change to the app usually changes `demo/demo.cast` (and `demo/demo.gif`) too, and they're committed with it.

Line and branch coverage of `src/assess_diff` and `tests` is held at 100% (`fail_under` in `pyproject.toml`),
so new code comes with the tests that run it, UI code included (driven headless through counterweight's autopilot).
Mutation testing aims for zero non-equivalent survivors;
each equivalent survivor is recorded in the `mutation-testing` project skill (`.claude/skills/mutation-testing/`)
with why no test can catch it.

## Conventions

- Python 3.15, line length 120, strict mypy, ruff (config in `pyproject.toml`).
- Frozen, slotted dataclasses for internal values; pydantic models only for what crosses the disk boundary.
- Every saved review is user data: never write a code path that can drop or overwrite comments silently.
- A change someone using assess-diff would notice (a key, a screen, a fix, a file format)
  gets an entry in `CHANGELOG.md` under `Unreleased`, grouped per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
  A fix to code that hasn't been released yet folds into its feature's own entry, not `Fixed`.
- Changing a model that's written to disk (`Review`, `Preferences`) bumps its `version`
  and adds the migration from the last (`MIGRATIONS` in `review.py`), with a fixture of the old file,
  so files written before the change still load.
