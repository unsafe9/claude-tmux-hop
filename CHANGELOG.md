# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
