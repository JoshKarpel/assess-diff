"""Random working trees driven by random keys and clicks, to find the sequences no hand-written test thought of."""

from __future__ import annotations

import tempfile
from pathlib import Path

from counterweight.events import KeyPressed
from counterweight.events import MouseDown
from counterweight.events import MouseUp
from counterweight.keys import Key
from hypothesis import given
from hypothesis import settings
from hypothesis import strategies as st

from assess_diff.preferences import PreferencesFile
from assess_diff.storage import ReviewStore
from tests.conftest import Scratch
from tests.conftest import init_scratch
from tests.harness import HEIGHT
from tests.harness import WIDTH
from tests.harness import Harness
from tests.harness import click

LINES = st.sampled_from(["a", "b", "c", "", "def f():", "    return 1", "\tindented", "ünïcode ✓", "long " * 30])
TEXT = st.builds(
    lambda lines, ends_with_newline: "\n".join(lines) + ("\n" if lines and ends_with_newline else ""),
    st.lists(LINES, max_size=15),
    st.booleans(),
)
CONTENT = TEXT | st.just(b"\0binary")
PATHS = st.sampled_from(["app.py", "notes.md", "src/deep/module.py", "with space.txt", 'quote"d.txt', "ünï.txt"])
TREE = st.dictionaries(PATHS, st.tuples(st.none() | CONTENT, st.none() | CONTENT), max_size=4)
"""Each path's committed content and working-tree content, where `None` is no file."""

# Every key the app or one of its dialogs binds, and a few that only a text field reads; `q` would end the run early.
KEYS = st.sampled_from(
    [
        *(Key.Up, Key.Down, Key.ShiftUp, Key.ShiftDown, Key.PageUp, Key.PageDown, Key.Home, Key.End),
        *(Key.Left, Key.Right, Key.Tab, Key.BackTab, Key.Enter, Key.Escape, Key.Backspace, Key.Space),
        *("j", "k", "J", "K", "n", "N", "r", "d", "e", "s", "z", "b", "t", "f", "o", "c", "x", "/"),
    ]
).map(lambda key: [KeyPressed(key=key)])
CLICKS = st.builds(click, st.integers(0, WIDTH - 1), st.integers(0, HEIGHT - 1))
STEPS = st.lists(KEYS | CLICKS, max_size=60).map(lambda steps: [event for step in steps for event in step])


def build_tree(scratch: Scratch, tree: dict[str, tuple[str | bytes | None, str | bytes | None]]) -> None:
    """Commits each path's old content, then leaves its new content in the working tree."""
    for path, (old, _) in tree.items():
        if old is not None:
            scratch.write(path, old)
    scratch.commit()
    for path, (_, new) in tree.items():
        if new is None:
            (scratch.root / path).unlink(missing_ok=True)
        else:
            scratch.write(path, new)


@settings(max_examples=100, deadline=None)
@given(tree=TREE, steps=STEPS)
async def test_random_keys_and_clicks_over_a_random_diff_never_crash(
    tree: dict[str, tuple[str | bytes | None, str | bytes | None]], steps: list[KeyPressed | MouseDown | MouseUp]
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scratch = init_scratch(root / "repo")
        build_tree(scratch, tree)
        harness = Harness(
            scratch=scratch,
            store=ReviewStore(root / "reviews"),
            preferences=PreferencesFile(root / "preferences.json"),
        )

        await harness.run(steps)
