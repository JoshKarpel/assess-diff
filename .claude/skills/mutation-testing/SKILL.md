---
name: mutation-testing
description: >
  assess-diff's mutation testing: running mutmut here without exhausting the VM's memory,
  this repo's traps (warm row caches, a Pygments lexer that allocates forever), and the
  catalogue of known equivalent survivors with why no test can catch each.
  MUST be invoked, alongside the global mutation-testing-python skill, whenever running
  `just mutate`, triaging or killing survivors in this repo, judging whether one is equivalent,
  or changing a function that a catalogued survivor sits in.
---

# Mutation testing assess-diff

This covers what is specific to this repo.
How to use mutmut, write killing tests, and argue equivalence is in the global
`mutation-testing-python` skill.

## Running it

- `just mutate` runs every mutant without a cached verdict;
  `just mutate 'assess_diff.diff.*'` re-runs the named ones.
- A confirming run is cold and capped:

  ```bash
  rm -rf mutants
  systemd-run --user --scope -q -p MemoryMax=5G -p MemorySwapMax=0 just mutate
  ```

- The round is done when every survivor matches an entry in the catalogue below.

## Memory

The VM has no swap, so a runaway allocation isn't killed promptly:
it thrashes the machine until it goes down.
`tests/conftest.py` caps the address space of mutmut's children, and nothing else.
Every hand-check, probe, and ad hoc `pytest` or `python` run outside mutmut is uncapped,
so run each in a capped scope:

```bash
systemd-run --user --scope -q -p MemoryMax=1500M -p MemorySwapMax=0 \
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<copy> uv run pytest ...
```

Don't sweep Pygments lexers.
counterweight's `highlight` builds its lexer with `stripnl=False`,
and with that Pygments 2.21.0's TNT lexer allocates without bound on `"\n\na"`
before yielding a single token.
A probe over every lexer took the VM down this way.

## Warm caches

mutmut forks each mutant's child from a parent that has already run the suite,
so module-level memos are warm.
`unified_chunks` and `split_chunks` in `ui/rows.py` are `lru_cache`d,
and without the autouse `cold_row_caches` fixture in `tests/test_rows.py`
every mutant of the row-building functions behind them survives.
A new memo in front of mutable code needs the same fixture.

A hand-check against a fresh copy of the source can't reproduce this.
To match mutmut, run the tests once, swap the mutated function's `__code__`
into the live function object, then run them again in the same process.

## Equivalent survivors

Mutant numbers change with every source edit,
so each entry names the function and the change instead.
A survivor that matches no entry is a hole until shown otherwise.

### `diff.py`

- `header_path`: the quoted-path scan starts at `end = 2` instead of `1`.
  `compute_diff` passes `--src-prefix=a/`, so a quoted body always begins `"a/`:
  index 1 is `a`, never a quote or a backslash, and the scan finds the same closing quote.

### `export.py`

- `excerpt_block`: the longest backtick run defaults to `1` instead of `0`.
  It's read only through `max(3, longest + 1)`, which is 3 either way.

### `git.py`

- `git`: `core.quotePath=false` lower- or upper-cased.
  git matches config names and parses booleans case-insensitively;
  probed on a non-UTF-8 path, both spellings print it raw,
  while `core.quotePath=true` quotes it.
- `default_branch`: `--quiet` dropped, or spelled `--QUIET`.
  With `--verify`, `--quiet` only turns a missing ref's exit 128 into a silent exit 1.
  Both raise `GitError`, which the loop discards, and an existing ref resolves either way.

### `ui/diff_view.py`

- `press_rows`, `press`, `finish_on_escape`: the trailing `case _: return None` dropped.
  Falling off the end returns `None` too.
  The case stays because mypy strict reports a missing return without it.
- `use_stable_actions`: `use_ref(actions)` becomes `use_ref(None)`.
  The next line sets `latest.current = actions` on every render, the first included,
  before anything reads the ref.
- `near_cursor`: `zip(..., strict=True)` weakened to `strict=None`, `strict=False`, or dropped.
  `pairwise` over the files' starts and the end yields exactly one span per file,
  so both arguments are the same length; `strict=True` guards that rather than changing anything.

### `ui/rows.py`

- `window`: any mutant that widens the slice of chunks the window is cut from:
  `first` lowered (`- 2`), `stop` set to `None` or found from `offset + width + 2`,
  or `lo` or `hi` dropped from the `bisect_left` that finds `stop`.
  The slice only spares `scrolled` and `fitted` the chunks outside the window,
  and cutting the window from more of the text draws the same cells:
  `test_window_draws_what_scrolling_and_fitting_the_whole_text_draws` compares it with cutting from all of it.
  These cost only time, which `just bench` measures and no test does.

### `ui/syntax.py`

- `highlighted`: `TAB_SIZE` dropped from the `highlight` call.
  counterweight's `tab_size` defaults to 4, which equals `TAB_SIZE`.
  This holds only while they stay equal; changing either makes the mutant killable.
- `highlighted`: `zip(..., strict=True)` weakened to `strict=None`, `strict=False`, or dropped.
  These guard an invariant rather than being strictly equivalent.
  With `stripnl=False, ensurenl=False`, Pygments yields exactly `len(lines) - 1` newlines
  for text holding no `\r` (which `highlighted` strips first),
  so both of `zip`'s arguments have the same length.
  A lexer breaking that would make `strict=True` raise where the mutant silently truncates;
  none is known.
