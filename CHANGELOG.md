# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.6] - 2026-08-26

### Changed

- The inbox preview (`@hop-inbox-preview`) and cycle position feedback
  (`@hop-cycle-feedback`) are now opt-in, defaulting to off. Both change how
  long-standing interactions look and feel, so upgrading no longer alters the
  behavior of an existing setup. Turn either on with `'on'`.
- The `@hop-status-next` badge takes the pane's state color as foreground and
  no longer paints a background. Unlike the second status line, which is a list
  where background pills separate one pane from the next, this badge sits
  inline among unrelated status segments, where a pill competes with whatever
  else shares the bar. `@hop-status-next-style` still accepts any tmux style,
  so `fg=colour235 bg=colour110` restores a pill.

## [0.8.5] - 2026-08-26

### Added

- The notification inbox (`prefix + i`) now shows the highlighted pane's live
  terminal content beside the list, so you can read what a session is actually
  asking without hopping to it and triage the whole queue in one pass. `ctrl-/`
  toggles the preview; `@hop-inbox-preview` turns it off entirely and
  `@hop-inbox-preview-lines` sets how much of the pane to show.
- A new status source, `@hop-status-next`, renders just the top-priority pending
  pane as a single clickable badge — the one thing to do right now, in one slot
  of your status bar. `@hop-status-next-format` shapes the label from `{icon}`,
  `{project}`, `{branch}`, `{reason}`, `{age}` and `{task}` tokens, and
  `@hop-status-next-style` recolors it. It derives from the same ordering and
  dismiss filter as every other view, so it can never disagree with them.
- Cycling with `prefix + Space` now reports your position in the queue being
  swept, e.g. `[2/3 waiting] palm-server · permission · 4m`, so it is obvious
  when you have seen everything. Set `@hop-cycle-feedback off` to hop silently.

### Fixed

- Branch names, repository directories and task summaries are escaped before
  they reach the status bar or a tmux message. tmux re-expands `#{...}` and
  `#[...]` in those places, so text containing them previously rendered wrong or
  bled styling into the rest of the line.

## [0.8.4] - 2026-06-12

### Fixed

- Auto-hop (`@hop-auto`) and terminal focus (`@hop-focus-app`) now fire only when
  a pane's state actually changes, not every time the same state is re-asserted.
  Claude Code re-emits the idle notification on a timer after a turn ends, which
  previously re-triggered the hop/focus with no dedup — yanking you back to a
  pane you had already left, seconds after returning to your work. The OS
  notification keeps its own dedup, so alerts are unaffected.

## [0.8.3] - 2026-06-11

### Changed

- The optional second status line (`@hop-status-inbox`) now renders each pending
  pane as a background-colored badge instead of flat `│`-separated text, so it
  reads like the window list.

### Added

- Clicking a badge jumps straight to that pane (switching session, window, and
  pane) when tmux mouse mode is on — no keybinding required.
- Per-state badge colors are configurable via `@hop-status-inbox-waiting-style`
  and `@hop-status-inbox-idle-style` (tmux style strings). Setting either to an
  empty string disables coloring for that state; the badge stays listed and
  clickable, and the state icon still distinguishes it.
