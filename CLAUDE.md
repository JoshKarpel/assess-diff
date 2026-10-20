# CLAUDE.md

## Project Overview

`assess-diff` is a terminal UI for reviewing a working tree's diff while an agent is still changing it,
and handing the review back to the agent as Markdown.
It's built on [counterweight](https://github.com/JoshKarpel/counterweight) (React-like TUI framework),
which this project tracks at `main` through a uv git dependency.
`PLAN.md` holds the goals, the design decisions and their reasoning, and the steps.

When counterweight lacks something assess-diff needs, the fix belongs upstream in counterweight,
not as a workaround here.
The counterweight checkout at `~/projects/counterweight` has its docs in `docs/` and its API in `src/`.

## Layout

- `src/assess_diff/` is split functional core / imperative shell:
  pure modules (diff parsing and row building, navigation, Markdown export, the review model)
  take values and return values;
  the shell (git subprocesses, the file watcher, review storage, the CLI) does the I/O and feeds them.
- Components live in `src/assess_diff/ui/`. They hold UI state; anything worth a unit test goes in a pure function beside them.

## Development

```bash
mise install   # just, uv
uv sync
just test      # mypy, then pytest with the coverage gate
just mutate    # mutmut, or `just mutate 'assess_diff.diff.*'` to re-run some mutants
just bench     # per-key latency on generated diffs, or `just bench --only large/down` for one workload
just profile large/down  # that workload under the sampling profiler, with a flamegraph in profiles/
just pre-commit
```

A performance claim needs `just bench` numbers from before and after the change, from the same machine.
The benchmarks in `benchmarks/` sit outside the coverage gate, and reuse the tests' harness.

Line and branch coverage of `src/assess_diff` and `tests` is held at 100% (`fail_under` in `pyproject.toml`),
so new code comes with the tests that run it, UI code included (driven headless through counterweight's autopilot).
Mutation testing aims for zero non-equivalent survivors;
each equivalent survivor is recorded in `docs/mutation-testing.md` with why no test can catch it.

## Conventions

- Python 3.15, line length 120, strict mypy, ruff (config in `pyproject.toml`).
- Frozen, slotted dataclasses for internal values; pydantic models only for what crosses the disk boundary.
- Every saved review is user data: never write a code path that can drop or overwrite comments silently.
- Changing a model that's written to disk (`Review`, `Preferences`) bumps its `version`
  and adds the migration from the last (`MIGRATIONS` in `review.py`), with a fixture of the old file,
  so files written before the change still load.
