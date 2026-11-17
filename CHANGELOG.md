# Changelog

## 0.2.0

### Added

- **`/` finds text in the diff**, such as a name.
  It lists the lines holding what's typed, starting from the cursor,
  and the diff behind it shows whichever match is highlighted:
  Enter goes there, and Escape puts the cursor back where it was.
  It ignores case unless what's typed has a capital letter,
  and `/` again returns to what was typed last.

### Changed

- **`b` picks the diff's base from the branch's commits**,
  where it used to toggle between `HEAD` and the merge base with the default branch.
  The picker lists the branch's first-parent commits under the working tree,
  newest first, back to the merge base,
  with a line from the working tree down to the highlighted commit,
  so both ends of the diff Enter would show are on screen together.
  `HEAD` and the merge base follow as they move;
  any other commit is pinned by its hash,
  so the diff keeps everything since it as the agent commits.
  On the default branch, the picker lists `HEAD`'s recent history instead.
- **Each changed file shows its hunks**, with three lines of context either side
  and a line saying how many unchanged lines are hidden between,
  where it used to show the whole file.
  Lines a comment is on stay in view with their context.
  `z` switches to whole files and back, and the choice is kept as a preference.
- **Reviews and preferences are saved as version 2**:
  a review records its chosen base, and preferences record whether to show hunks or whole files.
  Files 0.1.0 saved load as before, but once 0.2.0 saves them, 0.1.0 can't read them:
  it lists such a review among the files it can't read, and stops at startup on such a preferences file.
- A comment's number, and whether it's outdated,
  are drawn in the top border of its box rather than on a line of their own.
- The theme picker and the file filter list the option the typed text names exactly first.

### Fixed

- **The file tree's highlight stays on its file as files appear above it**,
  and the review manager's stays on its review when a rename moves it down the list.
  Both used to keep their position, which put the highlight on a different row.
- A key pressed right after a move, before the app redraws,
  acts on the row the move highlighted rather than the one still drawn highlighted:
  Enter in every picker, and `e` and `d` in the review manager.

## 0.1.0

### Added

- The first release: a terminal UI for reviewing a working tree's diff
  while an agent is still changing it,
  commenting on its lines,
  and handing the review back to the agent as Markdown.
