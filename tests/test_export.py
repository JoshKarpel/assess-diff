from __future__ import annotations

from assess_diff.diff import BaseMode
from assess_diff.diff import LineKind
from assess_diff.diff import Side
from assess_diff.export import excerpt_block
from assess_diff.export import to_markdown
from assess_diff.review import ExcerptLine
from tests.builders import added
from tests.builders import comment
from tests.builders import context
from tests.builders import diff_of
from tests.builders import modified
from tests.builders import removed
from tests.builders import review

FILE = modified(
    "src/app.py",
    context(1, 1, "def handler():"),
    removed(2, "    return None"),
    added(2, "    return compute()"),
)


def test_markdown_lists_comments_in_creation_order_with_references_and_excerpts() -> None:
    later_in_file = comment(
        1,
        "src/app.py",
        2,
        2,
        ((LineKind.REMOVED, "    return None"), (LineKind.ADDED, "    return compute()")),
        body="Why compute here?",
    )
    earlier_in_file = comment(
        2, "src/app.py", 1, 1, ((LineKind.CONTEXT, "def handler():"),), body="  Rename this, see #1.\n"
    )

    markdown = to_markdown(review(later_in_file, earlier_in_file, summary="  Mostly good.\n"), diff_of(FILE))

    assert markdown == (
        "# Review of `feature/x` against `trunk` (merge base `c0ffee0`)\n"
        "\n"
        "Mostly good.\n"
        "\n"
        "## 1. `src/app.py:2`\n"
        "\n"
        "```diff\n"
        "-    return None\n"
        "+    return compute()\n"
        "```\n"
        "\n"
        "Why compute here?\n"
        "\n"
        "## 2. `src/app.py:1`\n"
        "\n"
        "```diff\n"
        " def handler():\n"
        "```\n"
        "\n"
        "Rename this, see #1.\n"
    )


def test_markdown_against_head_and_a_detached_head() -> None:
    markdown = to_markdown(review(branch=None), diff_of(FILE, mode=BaseMode.HEAD, label="HEAD"))

    assert markdown == "# Review of detached `HEAD` against `HEAD` (`c0ffee0`)\n"


def test_markdown_names_a_range_and_marks_outdated_comments() -> None:
    stale = comment(3, "src/app.py", 1, 2, ((LineKind.ADDED, "gone"),), body="Stale.")

    markdown = to_markdown(review(stale), diff_of(FILE))

    assert markdown.split("\n\n")[1:] == [
        "## 3. `src/app.py:1-2`",
        "_These lines have changed since this comment was written; the excerpt shows them as they were._",
        "```diff\n+gone\n```",
        "Stale.\n",
    ]


def test_markdown_names_removed_lines_and_their_base() -> None:
    one = comment(1, "src/app.py", 2, 2, ((LineKind.REMOVED, "    return None"),), side=Side.OLD)
    several = comment(2, "src/app.py", 2, 5, ((LineKind.REMOVED, "x"),), side=Side.OLD)

    headings = [
        line for line in to_markdown(review(one, several), diff_of(FILE)).splitlines() if line.startswith("## ")
    ]

    assert headings == [
        "## 1. `src/app.py`, removed line 2 (in `c0ffee0`)",
        "## 2. `src/app.py`, removed lines 2-5 (in `c0ffee0`)",
    ]


def test_an_empty_comment_body_leaves_no_blank_section() -> None:
    markdown = to_markdown(
        review(comment(1, "src/app.py", 1, 1, ((LineKind.CONTEXT, "def handler():"),))), diff_of(FILE)
    )

    assert markdown.endswith("```diff\n def handler():\n```\n")


def test_excerpt_fence_is_longer_than_any_backtick_run_inside() -> None:
    excerpt = (ExcerptLine(kind=LineKind.ADDED, text='doc = """```python"""'),)

    assert excerpt_block(excerpt) == '````diff\n+doc = """```python"""\n````'
