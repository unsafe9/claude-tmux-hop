# Claude Tmux Hop

A tool for navigating between multiple Claude Code sessions in tmux panes with priority-based cycling.

## Tech Stack

- Python 3.10+ (standard library only, no external deps)

## Deployment

- I'll create a github release with `version-bump` command
- Version bump rules: Python source changes bump all three (`pyproject.toml`,
  `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`); plugin-file
  changes (`hooks/`, `plugin.json` itself) bump `plugin.json` +
  `marketplace.json`; pure marketplace-metadata edits bump nothing.
  Why: the plugin ships and executes the Python code via `bin/claude-tmux-hop`,
  so Python changes are always plugin-behavior changes, and
  `marketplace.json` (`plugins[*].version`) always mirrors `plugin.json`.

## Project Structure

```
src/claude_tmux_hop/
  cli.py          # CLI entry (argparse subcommands)
  parser.py       # CLI argument parser setup (argparse subcommands)
  tmux.py         # Tmux operations, PaneInfo dataclass
  priority.py     # State priority logic - see STATE_PRIORITY, PENDING_STATES
  paths.py        # XDG/TPM path detection - see get_tmux_config_paths()
  install.py      # Installation & update logic + environment checks (CheckResult)
  testing.py      # Self-tests - see run_all_tests()
  log.py          # Logging to ~/.local/state/claude-tmux-hop/hop.log
  notify/         # Notification & focus module (Strategy pattern)
hooks/
  hooks.json      # Hook definitions
skills/
  hop-status/SKILL.md       # Skill: summarize all tracked Claude sessions and states
  hop-config/SKILL.md       # Skill: inspect and edit @hop-* tmux options (persistent) + update conductor instructions
  hop-dispatch/SKILL.md     # Skill: route a task to another Claude pane — pick + execute one of 4 modes (switch / send-prompt / spawn-task / spawn-with-worktree); single source of truth for those CLI flags. Used by the conductor and by general routing requests.
hop.tmux          # TPM plugin entry point
```

## CLI Commands

Subcommands, flags, and help text are defined in `parser.py`; each maps to a
`cli.py:cmd_<name>()` handler. User-facing config options and key defaults
are documented in `README.md` — don't duplicate them here.

## Key Patterns

### State Priority
See `priority.py:STATE_PRIORITY`
- `waiting` (0): user input needed - newest first
- `idle` (1): task complete - newest first
- `active` (2): running - newest first

### Tmux State Storage
See `tmux.py:set_pane_state()`, `set_pane_git_identity()`, `get_hop_panes()`
- Pane options are the **single source of truth**: status bar, picker, cycle,
  and notification inbox all derive their views from `get_hop_panes()`, so
  they can never disagree and state dies with the pane (auto-cleanup)
- Options: `@hop-state`, `@hop-timestamp`, `@hop-task`, `@hop-wait-reason`
  (question/plan/permission/elicitation, kept only while `waiting`),
  `@hop-last-notify` (notification dedup stamp), `@hop-project`/`@hop-branch`
  (git identity — resolved on waiting/idle registers only; the hot-path
  `active` register skips the git call, and the inbox backfills missing
  identity once on open)

### Window Auto-Rename
See `tmux.py:rename_window()`, `cli.py:cmd_register()`
- `@hop-window-rename` (default: off): renames the window to
  `<state-icon> <dir basename>` on every register — the icon tracks state
  while the name stays a stable navigation label (worktree dirs naturally
  distinguish parallel work; the churning ai-title summary stays in
  inbox/picker/`@hop-task` instead). Icons honor the user's
  `@hop-status-format` tokens. With multiple claude panes in one window the
  icon shows the highest-priority state among them
  (`cli.py:_best_window_state()`, fed by `tmux.py:get_window_states()`).
  SessionEnd keeps the directory name as the window label; the icon falls
  back to the surviving panes' best state, or is dropped when none remain
  (tmux `automatic-rename` stays off).

### Path Detection
See `paths.py:get_tmux_config_paths()`, `get_tpm_plugin_paths()` — covers
XDG, traditional `~/.tmux.conf`, oh-my-tmux, and TPM (env var or standard
locations).

### Auto-Hop
See `cli.py:should_auto_hop()`, `do_auto_hop()`
- `@hop-auto`: comma-separated states to trigger (default: empty = disabled)
- `@hop-auto-priority-only`: only hop if highest priority (default: "on")

### Notification Inbox
See `cli.py:cmd_inbox()`, `_pending_panes()`
- A **view over pane options**, not a store: every tracked pane, attention
  first — waiting → idle → active, newest first within each group; stale
  waiting panes auto-flip to idle. Cycle uses the pending-only
  `_pending_panes()` view (active panes are listed, not cycled).
- Dismiss (ctrl-x) sets a global cleared-at stamp — a view filter, not a
  state change: pending panes older than the stamp are hidden from
  inbox/cycle until their next state change. Active rows are an overview,
  not notifications, so they stay; status bar counts are untouched.
- Project column = main-repo name (`@hop-project`) so worktree panes don't
  repeat the branch in both columns, falling back to the cwd basename; the
  branch column falls back on detached HEAD to the worktree dir name or a
  short SHA.
- Self-heal on open: hooks only fire on graceful exits, so a kill -9'd
  claude leaves stale state on its still-living pane — `cmd_inbox()` clears
  it, which also corrects the status bar. Gone panes need nothing (options
  die with the pane). A failed process scan (None) shows everything rather
  than mass-clearing live sessions.

### Notification & Focus (notify/)
Strategy pattern: `base.py` defines the protocols (`Notifier`,
`FocusHandler`, `FocusDetector`); each platform module (macos/linux/windows)
implements all three and registers in `__init__.py`.
- `@hop-notify` / `@hop-focus-app`: states that trigger OS notifications /
  terminal focus (default: empty = disabled); `@hop-terminal-app` overrides
  the auto-detected terminal app
- Focus and auto-hop are independent: app focus is OS-level (window/tab),
  auto-hop is tmux-level (pane). Both fire when configured for the same
  state; neither short-circuits the other.
- Smart suppression: notification and app focus are skipped when the
  terminal (and correct tab on macOS) is already focused. Auto-hop is
  **not** skipped — being on the terminal does not mean being on the right
  pane.
- Notification bodies carry the most informative detail available
  (permission message, pending question, or task summary on idle).
  Identical bodies per pane are deduped within `NOTIFY_COOLDOWN_SECONDS`;
  the dedup stamp resets on every `active` register so each new user turn
  notifies fresh.
- Click-to-focus (macOS only) uses `terminal-notifier` when installed
  (optional external dep); otherwise falls back to AppleScript
  notifications without a click action.

### Conductor
See `cli.py:cmd_conductor*()`, `tmux.py:spawn_conductor_session()`,
`install.py:update_conductor_instructions()`
- **Opt-in**: disabled by default; enable via `@hop-conductor-enabled`.
  While off, no keybinding registers and `conductor --popup` refuses with a
  hint. The dispatch primitives (`spawn-task`, `send-prompt`, `list --json`,
  `--update-instructions`, `--kill`, the context hooks) are general, not
  conductor-gated, and work regardless.
- **Persistent background session**: the conductor claude lives in a
  detached tmux session (`@hop-conductor-session`); the popup is just a
  viewer attached to it. Detaching keeps in-flight work running;
  re-attaching reuses the same claude. The session runs `exec claude`,
  tying its lifetime to claude — when claude exits the session dies and the
  next attach auto-recovers. The respawn binding kills the session first:
  destructive of in-flight state, required to pick up refreshed
  instructions.
- The session name doubles as a filter: panes in that session are excluded
  from every pane view (cycle/picker/discover/inbox) so the conductor never
  sees itself.
- **Workbench ownership**: `@hop-conductor-dir` belongs to the user — the
  plugin only creates the dir, never seeds a CLAUDE.md. Plugin-managed
  canon (dispatch-mode selection, safety rules) lives in
  `install.py:CONDUCTOR_INSTRUCTIONS` and, on disk, inside the
  `<conductor-instructions>` marker block that `--update-instructions`
  replaces; everything outside the marker is user-owned and preserved. A
  running claude read CLAUDE.md once at startup, so updated instructions
  take effect only after a respawn; keeping the on-disk block current
  across plugin updates is the user's responsibility.
- **Hook injection**: when the workbench CLAUDE.md lacks the marker,
  SessionStart injects the canon in-memory (`conductor-context`).
  UserPromptSubmit injects a fresh pane snapshot every turn
  (`conductor-prompt-context`, same JSON shape as `list --json`) so the
  conductor never has to poll pane state. Both no-op outside the workbench
  and swallow all errors.
- **`CLAUDE_TMUX_HOP_CONDUCTOR=1` fast-path gate**: injected at session
  creation so it reaches every pane in the conductor session; the two
  conductor hook commands in `hooks.json` are shell-guarded on it so
  non-conductor Claude sessions skip the Python interpreter entirely. The
  cwd check inside the handlers stays as a second guard.
- **Dispatch**: the conductor picks one of four modes per task — switch /
  send-prompt / spawn-task / spawn-task in a fresh worktree (the conductor
  runs `git worktree add` itself). Mode *selection* lives in the conductor
  instructions; the CLI *shape* for each mode lives only in the
  `hop-dispatch` skill, so flag changes land in one place. Safety:
  `send-prompt` refuses `active` panes unless forced.

### Hook Flow
`hooks/hooks.json` is the source; the state mapping in brief — all commands
carry an explicit `timeout: 10` so a hung tmux can never block Claude for
the 60s default:
- SessionStart (startup|resume) → idle + `conductor-context`
- UserPromptSubmit → active + `conductor-prompt-context`
- PreToolUse (AskUserQuestion → reason `question`; ExitPlanMode → reason `plan`) → waiting
- PostToolUse / PostToolUseFailure (AskUserQuestion|ExitPlanMode) → active
- Notification (permission_prompt → `permission`; elicitation_dialog → `elicitation`) → waiting; (idle_prompt) → idle
- Elicitation → waiting, reason `elicitation`; ElicitationResult → active
- Stop / StopFailure → idle
- SessionEnd → clear
- Intentionally *not* hooked: PreCompact, PostCompact, SessionStart(compact|clear),
  SubagentStart/Stop — these are infra events, not user-visible state
  transitions, so existing state is preserved.

## Code Conventions

- Uses dataclasses with type hints
- Don't import under functions unless it's necessary
- Extract magic numbers and constants out of scopes
- Well-structured and clean codes are already descriptive without verbose comments
- When implementing sharable codes, check duplication and consider modularizing
