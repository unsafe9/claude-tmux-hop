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
  priority.py     # State priority logic - see STATE_PRIORITY
  inbox.py        # Notification inbox - JSONL storage in ~/.local/state/claude-tmux-hop/inbox.jsonl
  paths.py        # XDG/TPM path detection - see get_tmux_config_paths()
  doctor.py       # Environment checks - see run_all_checks()
  install.py      # Installation & update logic
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
  hooks.json      # Hook definitions (9 hooks)
skills/
  hop-status/SKILL.md       # Skill: summarize all tracked Claude sessions and states
  hop-config/SKILL.md       # Skill: inspect and edit @hop-* tmux options (persistent) + update conductor instructions
  hop-dispatch/SKILL.md     # Skill: route a task to another Claude pane — pick + execute one of 4 modes (switch / send-prompt / spawn-task / spawn-with-worktree); single source of truth for those CLI flags. Used by the conductor and by general routing requests.
hop.tmux          # TPM plugin entry point
```

## CLI Commands

See `cli.py:main()` for full command definitions.

```bash
claude-tmux-hop <command>
  # Core commands
  register --state <s> [--reason <r>]  # Set state: waiting|idle|active (+ wait reason)
  clear                   # Remove hop state from pane
  cycle                   # Jump to next pane (priority order)
  back                    # Jump back to previous pane
  picker-data             # Output picker data for fzf
  switch <pane_id>        # Switch to specific pane (internal)
  list                    # Show all panes (auto-validates)
  discover                # Auto-discover Claude sessions
  prune                   # Remove stale panes
  status                  # Output status bar string
  inbox                   # Output notification inbox for display menu
  inbox-clear             # Clear notification inbox

  # Conductor primitives
  spawn-task              # New window + claude + prompt (in target session)
  send-prompt             # Inject prompt into existing claude pane
  conductor               # Open conductor popup or update its CLAUDE.md marker block
  conductor-context       # Internal: SessionStart hook emits in-memory instructions
  conductor-prompt-context # Internal: UserPromptSubmit hook emits fresh pane snapshot
  list --json             # Same listing as `list`, plus per-pane git context

  # Management commands
  install                 # Install tmux/claude plugins
  update                  # Update installed plugins
  doctor                  # Check environment
```

## Key Patterns

### State Priority
See `priority.py:STATE_PRIORITY`
- `waiting` (0): user input needed - newest first
- `idle` (1): task complete - newest first
- `active` (2): running - newest first

### Tmux State Storage
See `tmux.py:set_pane_state()`, `get_hop_panes()`
- Uses custom pane options: `@hop-state`, `@hop-timestamp`, `@hop-task`,
  `@hop-wait-reason` (question/plan/permission/elicitation — set by hooks via
  `register --reason`, only kept while state is `waiting`), `@hop-last-notify`
  (notification dedup stamp)

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
See `inbox.py`, `cli.py:cmd_inbox()`
- Records `waiting` and `idle` state changes to `~/.local/state/claude-tmux-hop/inbox.jsonl`
- `@hop-inbox-key`: keybinding to open inbox display-menu (default: "i")
- Max 50 entries stored, displays 20 most recent (priority order: waiting → idle, each group newest first; stale waiting panes auto-flip to idle)
- Each entry shows state icon, project name, time ago, wait reason (when waiting), task summary; clicking switches to pane

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
- **Persistent background session model**: the conductor lives in a detached tmux session (default name `conductor`, configurable via `@hop-conductor-session`). The popup is just a *viewer* attached via `tmux attach`. `prefix + d` inside the popup detaches without killing claude — anything in-flight (a dispatch loop, a long tool call) keeps running. Re-pressing `prefix + y` re-attaches to the same claude. The session is spawned by `spawn_conductor_session()` with `tmux new-session -d -e CLAUDE_TMUX_HOP_CONDUCTOR=1 -e PATH=<own-bin>:$PATH -s <session> -c <workbench> 'exec claude'`. `exec claude` ensures the session ends when claude exits, so the next attach attempt auto-recovers.
- Workbench dir = `@hop-conductor-dir` (default `~/.config/claude-tmux-hop/conductor/`, supports `~` and `$VAR` expansion). The user owns this directory entirely. `ensure_conductor_dir()` only creates the dir — **no CLAUDE.md is seeded**.
- **Plugin-managed instructions live behind a marker.** The canonical 4-mode dispatch table + safety rules + orchestration patterns are kept in `install.py:CONDUCTOR_INSTRUCTIONS` wrapped by `<conductor-instructions>` / `</conductor-instructions>` XML tags. Inside the block = plugin-owned (replaced by `--update-instructions`). Outside the block = user-owned (preserved across updates). Running `--update-instructions` while the conductor session is alive does **not** affect the running claude (it read CLAUDE.md once at startup) — the user must `prefix + Y` to respawn for the new canon to take effect.
- **SessionStart hook injects in-memory** when the workbench has no marker. `cmd_conductor_context()` runs on every Claude SessionStart but no-ops unless cwd == workbench AND `CLAUDE.md` lacks the marker. When it does fire, it emits `additionalContext` (model-only, the canonical instructions) plus `systemMessage` (user-only, hint to run `--update-instructions` to persist). If the marker is already in `CLAUDE.md`, no-op — claude reads it as cwd context naturally.
- **UserPromptSubmit hook injects fresh snapshot** every turn. `cmd_conductor_prompt_context()` runs on every Claude UserPromptSubmit but no-ops unless cwd == workbench. When it fires, it emits `additionalContext` (model-only) containing the same JSON shape as `list --json` (pane id, state, cwd, branch, worktree_root, project, ai-title `task`, etc.) so the conductor model never has to manually call `hop-status` or `list --json` per turn. The hook is defensive — any error (no tmux, unreadable options) is swallowed silently.
- **`CLAUDE_TMUX_HOP_CONDUCTOR=1` env var is the fast-path gate.** Injected via `tmux new-session -e` at session creation, so it propagates to every shell/pane in the conductor session (including any windows the user manually creates inside it). The two conductor-related hook commands in `hooks/hooks.json` are wrapped as `[ "$CLAUDE_TMUX_HOP_CONDUCTOR" = 1 ] && ... || true` so non-conductor Claude sessions skip the Python interpreter entirely (~108ms saved per prompt per session in our benchmarks). The cwd check inside the CLI handlers remains as a second guard for the edge case of someone exporting the var manually outside the conductor session.
- `@hop-conductor-popup-key` (default `y`): attach the popup to the conductor session (creates the session on demand) — prefix-key binding.
- `@hop-conductor-respawn-key` (default `Y`): kill the conductor session first, then attach to a fresh claude. **Destructive** of any in-flight state. Use when the user wants a clean slate or needs to pick up updated canon after `--update-instructions`.
- `@hop-conductor-session` (default `conductor`): both the *spawn target* (the persistent session the plugin creates and attaches to) and a *filter source* — any tmux session with this name is excluded from `get_hop_panes()`, `get_claude_panes_by_process()`, and `inbox.record()` so the conductor itself never pollutes cycle/picker/discover/inbox.
- Subcommands:
  - `conductor --popup [--respawn] | --update-instructions [--force] | --kill` — attach the popup (with `--respawn`, kill the session first); refresh the plugin-managed marker block in the workbench `CLAUDE.md`; or tear down the conductor session without opening a popup. `--force` (with `--update-instructions`) is required if `CLAUDE.md` exists without a marker; it backs up the file to `CLAUDE.md.bak` before overwriting. `--kill` is idempotent (no-op if no session).
  - `conductor-context` — internal, invoked by SessionStart hook.
  - `conductor-prompt-context` — internal, invoked by UserPromptSubmit hook; emits fresh pane snapshot when cwd == workbench.
  - `list --json` — situational awareness (state + git context for each pane).
  - `spawn-task --session --cwd --prompt [--window-name] [--no-switch]` — new window + new claude + prompt; creates session if missing.
  - `send-prompt --pane --prompt [--no-switch] [--force]` — inject prompt into an existing claude pane. **CLI refuses `active` panes** unless `--force`.
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

