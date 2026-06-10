# Claude Tmux Hop

A tool for navigating between multiple Claude Code sessions in tmux panes with priority-based cycling.

## Tech Stack

- Python 3.10+ (standard library only, no external deps)

## Deployment

- I'll create a github release with `version-bump` command
- Version bump rules:
  - `pyproject.toml`: bump when Python source under `src/claude_tmux_hop/` changes.
  - `.claude-plugin/plugin.json`: bump when Python source OR plugin files (`hooks/`, `plugin.json` itself) change. The plugin ships and executes the Python code via `bin/claude-tmux-hop`, so Python changes always change plugin behavior.
  - `.claude-plugin/marketplace.json` (`plugins[*].version`): always mirror `plugin.json`. Bump whenever `plugin.json` bumps. Pure marketplace-metadata edits (description/owner) do not require a bump.
  - Consequence: Python changes bump all three; plugin-only changes bump `plugin.json` + `marketplace.json`; marketplace-metadata-only changes bump nothing.

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
    __init__.py   # Public API, registries, terminal detection
    base.py       # Protocols (Notifier, FocusHandler, FocusDetector), PaneContext
    macos.py      # macOS: AppleScript notifications, focus, tab detection
    linux.py      # Linux: notify-send, wmctrl/xdotool, X11 detection
    windows.py    # Windows: PowerShell toast, COM focus, Win32 detection
    terminals.py  # Terminal app detection mappings (bundle IDs, env vars)
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
`cli.py:cmd_<name>()` handler.

## Key Patterns

### State Priority
See `priority.py:STATE_PRIORITY`
- `waiting` (0): user input needed - newest first
- `idle` (1): task complete - newest first
- `active` (2): running - newest first

### Tmux State Storage
See `tmux.py:set_pane_state()`, `set_pane_git_identity()`, `get_hop_panes()`
- Pane options are the **single source of truth**: the status bar, picker,
  cycle, and notification inbox all derive their views from `get_hop_panes()`,
  so they can never disagree and state dies with the pane (auto-cleanup)
- Custom pane options: `@hop-state`, `@hop-timestamp`, `@hop-task`,
  `@hop-wait-reason` (question/plan/permission/elicitation — set by hooks via
  `register --reason`, only kept while state is `waiting`), `@hop-last-notify`
  (notification dedup stamp), `@hop-project` / `@hop-branch` (git identity,
  resolved on waiting/idle registers only — the frequent `active` register
  skips the git call)

### Window Auto-Rename
See `tmux.py:rename_window()`, `is_window_rename_enabled()`, `cli.py:cmd_register()`, `_get_state_icon()`
- `@hop-window-rename` (default: off): renames the pane's tmux window to
  `<state-icon> <dir basename>` on every register — the icon tracks state
  while the name stays a stable navigation label (worktree dirs naturally
  distinguish parallel work; the churning ai-title summary stays in
  inbox/picker/`@hop-task` instead)
- State icons honor the user's `@hop-status-format` `{state:icon}` tokens,
  falling back to `STATE_ICONS` for states the format omits
- SessionEnd restores tmux `automatic-rename` for the window

### Path Detection
See `paths.py:get_tmux_config_paths()`, `get_tpm_plugin_paths()`
- XDG: `$XDG_CONFIG_HOME/tmux/tmux.conf`
- Traditional: `~/.tmux.conf`
- oh-my-tmux: `~/.tmux.conf.local`
- TPM: Detects via `TMUX_PLUGIN_MANAGER_PATH` env or standard locations

### Auto-Hop
See `cli.py:should_auto_hop()`, `do_auto_hop()`
- `@hop-auto`: comma-separated states to trigger (default: empty = disabled)
- `@hop-auto-priority-only`: only hop if highest priority (default: "on")

### Notification Inbox
See `cli.py:_pending_panes()`, `cmd_inbox()`, `cmd_inbox_clear()`, `_format_inbox_lines()`
- A **view over pane options**, not a store: panes in `PENDING_STATES`
  (waiting/idle), priority order (waiting → idle, each group newest first;
  stale waiting panes auto-flip to idle), top 20 shown. Cycle uses the same
  `_pending_panes()` view. Project column = `@hop-project` (main-repo name —
  worktree panes don't duplicate the branch in the project column) falling
  back to the cwd basename; branch column = `@hop-branch`, falling back on
  detached HEAD to the linked worktree's directory name or `@<short-sha>`
  in the main checkout
- `@hop-inbox-key` (default: "i"): opens an fzf popup (enter: jump, ctrl-x:
  clear all) with display-menu fallback when fzf/popup is unavailable
- ctrl-x sets the `@hop-inbox-cleared-at` global stamp — a view filter, not a
  state change: panes whose timestamp predates it are hidden from inbox/cycle
  until their next state change; status bar counts are untouched
- Self-heal on open: hooks only fire on graceful exits, so a kill -9'd claude
  leaves stale state on its still-living pane. `cmd_inbox()` clears such
  panes' state (which also corrects the status bar). Gone panes need nothing —
  their options died with them. A failed process scan
  (`get_running_claude_pane_ids()` → None) shows everything rather than
  mass-clearing live sessions. Pre-0.7 `inbox.jsonl` leftovers are deleted on
  open
- Entries render as aligned columns — state icon (honors `@hop-status-format`),
  session:window, project, branch, time ago, wait reason, task summary —
  padded to the widest cell; columns empty across all entries are dropped.
  `inbox --ansi` adds per-column colors for fzf; the menu fallback stays plain
  (display-menu can't render ANSI codes)

### Notification & Focus (notify/)
Cross-platform notification and terminal focus using Strategy pattern:
- `@hop-notify`: states that trigger OS notifications (default: empty = disabled)
- `@hop-focus-app`: states that trigger terminal focus (default: empty = disabled)
- `@hop-terminal-app`: override auto-detected terminal app name

**Architecture (Strategy Pattern):**
- `base.py`: Protocols (`Notifier`, `FocusHandler`, `FocusDetector`), `PaneContext` dataclass
- Platform implementations registered in `__init__.py` (`NOTIFIERS`, `FOCUS_HANDLERS`, `FOCUS_DETECTORS`)
- Each platform module (macos/linux/windows) implements all three protocols

**Flow in `cmd_register()`:**
1. Build `PaneContext` from current pane (pane_id, session, window, project)
2. Record to notification inbox (waiting/idle only)
3. Call `handle_state_notifications(state, project, pane_context, detail)`:
   - If `@hop-focus-app` matches: focus terminal app/tab only (OS-level)
   - If `@hop-notify` matches and terminal not focused: send OS notification
   - `detail` enriches the body (`cli.py:_notify_detail()`): the Notification
     hook's `message` (permission prompt text), the pending AskUserQuestion
     text, or the task summary on idle
   - Dedup: identical bodies for the same pane are suppressed within
     `NOTIFY_COOLDOWN_SECONDS` (120s) via the `@hop-last-notify` pane option;
     the stamp resets on every `active` register (new user turn)
4. If `@hop-auto` matches: call `do_auto_hop(pane_context)` → switch to target
   `session:window.pane` via tmux (no-op when already on that pane)

Focus (`@hop-focus-app`) and auto-hop (`@hop-auto`) are independent actions
and both fire when configured for the same state. App focus handles the OS
window/tab; auto-hop handles tmux pane navigation. They do not short-circuit
each other.

**Smart Suppression:**
- `is_terminal_focused()` checks if user is already looking at the terminal
- Skips notification and app focus if terminal (and correct tab on macOS) is focused
- Does **not** skip auto-hop — being on the terminal does not mean being on the right pane

**Click-to-Focus (macOS only, optional):**
- Uses `terminal-notifier` if installed (external dep, not required)
- Falls back to AppleScript `display notification` (no click action)

### Conductor
See `cli.py:cmd_conductor()`, `cli.py:cmd_conductor_context()`, `cli.py:cmd_conductor_prompt_context()`, `tmux.py:spawn_conductor_session()`, `kill_session_if_exists()`, `spawn_window()`, `send_prompt_to_pane()`, `resolve_conductor_dir()`, `install.py:update_conductor_instructions()`.
- **Opt-in**: disabled by default. Enable with `tmux set -g @hop-conductor-enabled on` (also `1`/`true`/`yes`). While off, no keybinding registers and `conductor --popup` refuses with a hint. `--update-instructions`, `--kill`, `list --json`, `spawn-task`, `send-prompt`, `conductor-context`, `conductor-prompt-context` work regardless — they are general primitives, not conductor-gated.
- **Persistent background session model**: the conductor lives in a detached tmux session (default name `conductor`, configurable via `@hop-conductor-session`). The popup is just a *viewer* attached via `tmux attach`. `prefix + d` inside the popup detaches without killing claude — anything in-flight (a dispatch loop, a long tool call) keeps running. Re-pressing `prefix + y` re-attaches to the same claude. The session is spawned by `spawn_conductor_session()` running `exec claude`, which ties the session's lifetime to claude — when claude exits the session dies, so the next attach attempt auto-recovers.
- Workbench dir = `@hop-conductor-dir` (default `~/.config/claude-tmux-hop/conductor/`, supports `~` and `$VAR` expansion). The user owns this directory entirely. `ensure_conductor_dir()` only creates the dir — **no CLAUDE.md is seeded**.
- **Plugin-managed instructions live behind a marker.** The canonical 4-mode dispatch table + safety rules + orchestration patterns are kept in `install.py:CONDUCTOR_INSTRUCTIONS` wrapped by `<conductor-instructions>` / `</conductor-instructions>` XML tags. Inside the block = plugin-owned (replaced by `--update-instructions`). Outside the block = user-owned (preserved across updates). Running `--update-instructions` while the conductor session is alive does **not** affect the running claude (it read CLAUDE.md once at startup) — the user must `prefix + Y` to respawn for the new canon to take effect.
- **SessionStart hook injects in-memory** when the workbench has no marker. `cmd_conductor_context()` runs on every Claude SessionStart but no-ops unless cwd == workbench AND `CLAUDE.md` lacks the marker. When it does fire, it emits `additionalContext` (model-only, the canonical instructions) plus `systemMessage` (user-only, hint to run `--update-instructions` to persist). If the marker is already in `CLAUDE.md`, no-op — claude reads it as cwd context naturally.
- **UserPromptSubmit hook injects fresh snapshot** every turn. `cmd_conductor_prompt_context()` runs on every Claude UserPromptSubmit but no-ops unless cwd == workbench. When it fires, it emits `additionalContext` (model-only) containing the same JSON shape as `list --json` (pane id, state, cwd, branch, worktree_root, project, ai-title `task`, etc.) so the conductor model never has to manually call `hop-status` or `list --json` per turn. The hook is defensive — any error (no tmux, unreadable options) is swallowed silently.
- **`CLAUDE_TMUX_HOP_CONDUCTOR=1` env var is the fast-path gate.** Injected via `tmux new-session -e` at session creation, so it propagates to every shell/pane in the conductor session (including any windows the user manually creates inside it). The two conductor-related hook commands in `hooks/hooks.json` are shell-guarded on this var so non-conductor Claude sessions skip the Python interpreter entirely. The cwd check inside the CLI handlers remains as a second guard for the edge case of someone exporting the var manually outside the conductor session.
- `@hop-conductor-popup-key` (default `y`): attach the popup to the conductor session (creates the session on demand) — prefix-key binding.
- `@hop-conductor-respawn-key` (default `Y`): kill the conductor session first, then attach to a fresh claude. **Destructive** of any in-flight state. Use when the user wants a clean slate or needs to pick up updated canon after `--update-instructions`.
- `@hop-conductor-session` (default `conductor`): both the *spawn target* (the persistent session the plugin creates and attaches to) and a *filter source* — any tmux session with this name is excluded from `get_hop_panes()`, `get_claude_panes_by_process()`, and `inbox.record()` so the conductor itself never pollutes cycle/picker/discover/inbox.
- Subcommands (flags live in `parser.py` / the `hop-dispatch` skill):
  - `conductor` — `--popup` attaches the viewer (`--respawn` kills the session first); `--update-instructions` refreshes the plugin-managed marker block in the workbench `CLAUDE.md` (requires `--force` when a markerless `CLAUDE.md` exists; backs it up to `CLAUDE.md.bak`); `--kill` tears down the session, idempotent.
  - `conductor-context` / `conductor-prompt-context` — internal, hook-invoked (SessionStart / UserPromptSubmit).
  - `list --json` — situational awareness (state + git context for each pane).
  - `spawn-task` — new window + new claude + prompt; creates the target session if missing.
  - `send-prompt` — inject prompt into an existing claude pane. **CLI refuses `active` panes** unless forced.
- Four dispatch modes the conductor picks among per task: (a) navigate via `switch`, (b) inject via `send-prompt`, (c) new window in project root via `spawn-task`, (d) new worktree (conductor runs `git worktree add` itself) then `spawn-task`. The conductor's instructions describe *which mode to pick*; the actual CLI shape for each mode lives in the `hop-dispatch` skill so flag changes only need to land in one place. The on-disk CONDUCTOR_INSTRUCTIONS marker block can go stale across plugin updates — the dispatch logic still works (skills travel with the binary) but the user is responsible for running `hop-config`'s "update conductor instructions" + `prefix + Y` (respawn) to pick up the refreshed copy.

### Hook Flow (hooks.json)
All hook commands carry an explicit `timeout: 10` so a hung tmux can never
block Claude for the 60s default.
- SessionStart (startup|resume) → idle + `conductor-context` (in-memory instruction inject when needed)
- UserPromptSubmit → active + `conductor-prompt-context` (fresh pane snapshot when in workbench)
- PreToolUse (AskUserQuestion) → waiting, reason `question`
- PreToolUse (ExitPlanMode) → waiting, reason `plan`
- PostToolUse / PostToolUseFailure (AskUserQuestion|ExitPlanMode) → active
- Notification (permission_prompt) → waiting, reason `permission`
- Notification (elicitation_dialog) → waiting, reason `elicitation`
- Notification (idle_prompt) → idle
- Elicitation → waiting, reason `elicitation` (MCP server requests user input)
- ElicitationResult → active (user responded to MCP elicitation)
- Stop / StopFailure → idle
- SessionEnd → clear
- Intentionally *not* hooked: PreCompact, PostCompact, SessionStart(compact|clear),
  SubagentStart/Stop — these are infra events, not user-visible state transitions,
  so existing state is preserved.

## Code Conventions

- Uses dataclasses with type hints
- Don't import under functions unless it's necessary
- Extract magic numbers and constants out of scopes
- Well-structured and clean codes are already descriptive without verbose comments
- When implementing sharable codes, check duplication and consider modularizing

