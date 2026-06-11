# Claude Tmux Hop

Navigate between multiple Claude Code sessions in tmux panes with priority-based cycling.

## Tech Stack

- Python 3.10+ (standard library only, no external deps)

## Deployment

- Releases — version bump (per-file rules), CHANGELOG, and the GitHub release —
  are handled by the `version-bump` project skill (`.claude/skills/version-bump/`).
  No release CI; the skill cuts the release directly via `gh`.

## Project Structure

```
src/claude_tmux_hop/
  cli.py          # CLI entry — cmd_<name>() handlers
  parser.py       # argparse subcommand definitions
  tmux.py         # tmux ops, PaneInfo
  priority.py     # state priority / sort order
  paths.py        # tmux.conf + TPM path detection
  install.py      # install / update / env checks
  testing.py      # self-tests (run_all_tests())
  log.py          # logging
  notify/         # notification + focus (Strategy pattern)
hooks/hooks.json  # hook → state mapping
skills/           # hop-status, hop-config, hop-dispatch
hop.tmux          # TPM plugin entry point
```

## CLI Commands

Subcommands, flags, and help text are defined in `parser.py`; each maps to a
`cli.py:cmd_<name>()` handler. User-facing config options and key defaults
are documented in `README.md` — don't duplicate them here.

## Key Invariants & Design Intent

What the code can't tell you fast. Follow the anchors for behavior.

### State lives in pane options (single source of truth)
- Pane options are the **only** state store. Every view — status bar, second
  status line, picker, cycle, inbox — derives from `get_hop_panes()`, so they
  can't disagree and state dies with the pane (auto-cleanup). Never add a
  parallel store.
- `@hop-*` options: state, timestamp, task, wait-reason (question/plan/
  permission/elicitation, kept only while `waiting`), git identity
  (project/branch), notify dedup stamp.
- Hot path: the `active` register skips the git-identity call; only waiting/idle
  resolve it, and the inbox backfills missing identity once on open.
- Anchors: `tmux.py:set_pane_state()`, `set_pane_git_identity()`, `get_hop_panes()`.

### Priority & views
- Order `waiting` (0) > `idle` (1) > `active` (2), newest first within each
  (`priority.py:STATE_PRIORITY`, `PENDING_STATES`).
- Inbox/cycle/status are **views, not stores**. Cycle uses pending-only
  (`_pending_panes()`); active panes are listed, not cycled.
- Dismiss (inbox ctrl-x) is a **view filter** — a global cleared-at stamp hides
  pending panes until their next state change. It never mutates state, so status
  counts are untouched and active rows (an overview) are never hidden.
- Self-heal on inbox open: hooks fire only on graceful exit, so a `kill -9`'d
  claude leaves stale state on its live pane — `cmd_inbox()` clears it. A failed
  process scan (None) shows everything rather than mass-clearing live sessions.
- Anchors: `cli.py:cmd_inbox()`, `_pending_panes()`.

### Status sources
- `@hop-status` (counts) and `@hop-status-inbox` (pending-pane list, for a second
  status line) are status **sources the plugin sets** via `hop.tmux`; treat as
  internal, not user-edited. Both stay light for the polling path (no self-heal/scan).
- `@hop-status-inbox` renders each pane as a bg-colored badge (`STATE_TMUX_BADGE`)
  wrapped in `#[range=pane|<id>]`. Click-to-hop is **free**: tmux's default
  `MouseDown1Status` is `switch-client -t =`, and switch-client to a pane target
  moves session+window+pane — so no keybinding is registered. The `#[range=...]`
  markers survive the `#(...)` substitution exactly like the `#[fg=...]` style codes.

### Window auto-rename
- `@hop-window-rename` (default off): window name = `<state-icon> <dir basename>`
  — the icon tracks state while the name stays a stable nav label (worktree dirs
  distinguish parallel work). Icons honor `@hop-status-format`; a multi-pane
  window shows the highest-priority state; tmux `automatic-rename` stays off.
- Anchors: `cli.py:cmd_register()`, `_best_window_state()`, `tmux.py:rename_window()`.

### Auto-hop & focus (independent)
- `@hop-auto` (default empty = off) auto-switches the tmux pane;
  `@hop-auto-priority-only` (default on) suppresses unless highest priority.
  `@hop-notify` / `@hop-focus-app` (default empty) fire OS notification / app
  focus; `@hop-terminal-app` overrides terminal detection.
- App focus (OS-level) and auto-hop (tmux-level) are **independent** — both fire,
  neither short-circuits. Smart suppression skips notify/focus when the terminal
  (and correct tab on macOS) is already focused, but **not** auto-hop (on the
  terminal ≠ on the right pane). Notify bodies dedup per pane within a cooldown,
  reset on every `active` register so each turn notifies fresh.
- `notify/` is a Strategy pattern (`base.py` protocols, per-OS modules register in
  `__init__.py`); macOS click-to-focus uses `terminal-notifier` if installed, else
  AppleScript. Anchors: `cli.py:should_auto_hop()`, `notify/`.

### Path detection
- `paths.py:get_tmux_config_paths()`, `get_tpm_plugin_paths()` — covers XDG,
  `~/.tmux.conf`, oh-my-tmux, and TPM (env var or standard locations).

### Conductor (opt-in)
- Disabled by default (`@hop-conductor-enabled`); while off no keybinding
  registers and `conductor --popup` refuses. The dispatch primitives
  (`spawn-task`, `send-prompt`, `list --json`, `--update-instructions`, `--kill`,
  context hooks) are general and work regardless.
- Persistent detached session (`@hop-conductor-session`) running `exec claude` —
  the popup is just a viewer; detach keeps work running; when claude exits the
  session dies and the next attach recovers. Respawn kills the session first
  (destructive; required to pick up refreshed instructions). The session name
  doubles as a filter: its panes are excluded from every pane view.
- Workbench (`@hop-conductor-dir`) is user-owned — the plugin creates the dir but
  never seeds a CLAUDE.md. Plugin canon lives in `install.py:CONDUCTOR_INSTRUCTIONS`
  and, on disk, inside the `<conductor-instructions>` marker that
  `--update-instructions` replaces; everything outside is preserved. A running
  claude reads CLAUDE.md once, so instruction changes need a respawn. When the
  workbench lacks the marker, SessionStart injects canon in-memory and
  UserPromptSubmit injects a fresh pane snapshot each turn (both no-op outside the
  workbench and swallow all errors).
- `CLAUDE_TMUX_HOP_CONDUCTOR=1` is injected at session creation; the conductor
  hook commands are shell-guarded on it so non-conductor sessions skip the Python
  interpreter entirely (the cwd check is a second guard).
- Dispatch: four modes (switch / send-prompt / spawn-task / spawn-task in a fresh
  worktree). Mode **selection** lives in the conductor instructions; the CLI
  **shape** lives only in the `hop-dispatch` skill, so flag changes land in one
  place. `send-prompt` refuses `active` panes unless forced.
- Anchors: `cli.py:cmd_conductor*()`, `tmux.py:spawn_conductor_session()`,
  `install.py:update_conductor_instructions()`.

### Hooks
- `hooks/hooks.json` is the source of truth for event → state mapping. Every
  command carries `timeout: 10` so a hung tmux can't block Claude for the 60s
  default.
- Intentionally **not** hooked: PreCompact, PostCompact, SessionStart(compact|clear),
  SubagentStart/Stop — infra events, not user-visible state transitions, so
  existing state is preserved.

## Code Conventions

- Uses dataclasses with type hints
- Don't import under functions unless it's necessary
- Extract magic numbers and constants out of scopes
- Well-structured and clean codes are already descriptive without verbose comments
- When implementing sharable codes, check duplication and consider modularizing
