from __future__ import annotations

import pytest
from counterweight.elements import Chunk
from counterweight.styles.utilities import text_color

from assess_diff.ui.file_picker import file_entry
from tests.builders import added
from tests.builders import modified
from tests.builders import removed

CHANGED = modified("src/b.py", removed(4, "four"), added(4, "vier"), added(5, "fünf"))


@pytest.mark.parametrize(("comments", "counts"), [(0, " +2 -1"), (3, " +2 -1 #3")], ids=["uncommented", "commented"])
def test_file_entry_shows_the_status_the_path_and_the_counts(comments: int, counts: str) -> None:
    assert file_entry(CHANGED, comments) == (
        Chunk(content="M ", style=text_color("amber", 300)),
        Chunk(content="src/b.py"),
        Chunk(content=counts, style=text_color("slate", 500)),
    )
