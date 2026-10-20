"""
A review as Markdown, for pasting into an agent's input: the summary, then each comment in creation order,
each with a line reference and the lines it was made on.
"""

from __future__ import annotations

import re
from typing import assert_never

from assess_diff.diff import BaseMode
from assess_diff.diff import Diff
from assess_diff.diff import LineKind
from assess_diff.review import Anchor
from assess_diff.review import Comment
from assess_diff.review import ExcerptLine
from assess_diff.review import NewAnchor
from assess_diff.review import OldAnchor
from assess_diff.review import Review
from assess_diff.review import is_outdated

MARKERS = {LineKind.CONTEXT: " ", LineKind.ADDED: "+", LineKind.REMOVED: "-"}


def to_markdown(review: Review, diff: Diff) -> str:
    sections = [heading(review, diff)]
    if review.summary.strip():
        sections.append(review.summary.strip())
    sections.extend(comment_section(comment, is_outdated(comment, diff)) for comment in review.comments)
    return "\n\n".join(sections) + "\n"


def heading(review: Review, diff: Diff) -> str:
    branch = f"`{review.branch}`" if review.branch else "detached `HEAD`"
    base = diff.base
    match base.mode:
        case BaseMode.HEAD:
            against = f"`HEAD` (`{base.short}`)"
        case BaseMode.MERGE_BASE:
            against = f"`{base.label}` (merge base `{base.short}`)"
        case _ as unreachable:
            assert_never(unreachable)
    return f"# Review of {branch} against {against}"


def location(anchor: Anchor) -> str:
    """
    Where a comment points, as an agent reads a file reference:
    `path:line` or `path:start-end` for the working tree's lines, and a note naming the base for removed ones.
    """
    lines = str(anchor.start) if anchor.start == anchor.end else f"{anchor.start}-{anchor.end}"
    match anchor:
        case NewAnchor():
            return f"`{anchor.path}:{lines}`"
        case OldAnchor():
            line_word = "line" if anchor.start == anchor.end else "lines"
            return f"`{anchor.path}`, removed {line_word} {lines} (in `{anchor.base_commit[:7]}`)"
        case _ as unreachable:
            assert_never(unreachable)


def comment_section(comment: Comment, outdated: bool) -> str:
    parts = [f"## {comment.number}. {location(comment.anchor)}"]
    if outdated:
        parts.append("_These lines have changed since this comment was written; the excerpt shows them as they were._")
    parts.extend((excerpt_block(comment.excerpt), comment.body.strip()))
    return "\n\n".join(part for part in parts if part)


def excerpt_block(excerpt: tuple[ExcerptLine, ...]) -> str:
    """The excerpt as a `diff` code block, fenced with more backticks than any run inside it."""
    body = "\n".join(f"{MARKERS[line.kind]}{line.text}" for line in excerpt)
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}diff\n{body}\n{fence}"
