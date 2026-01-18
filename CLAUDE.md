# Claude Tmux Hop

A tool for navigating between multiple Claude Code sessions in tmux panes with priority-based cycling.

## Tech Stack

- Python 3.10+ (standard library only, no external deps)
- `uv`

## Deployment

- I'll create a github release with `version-bump` command
  - It will bump the pyproject version
  - PyPI publish will be automatically triggered by github workflow
- Update the version of `.claude-plugin/plugin.json` file when the plugin has changed
- Update the version of `.claude-plugin/marketplace.json` file when the marketplace config has changed

## Project Structure

```
src/claude_tmux_hop/
  cli.py          # CLI entry (argparse subcommands)
  parser.py       # CLI argument parser setup (argparse subcommands)
  tmux.py         # Tmux operations, PaneInfo dataclass
  priority.py     # State priority logic - see STATE_PRIORITY
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
  hooks.json      # Hook definitions (7 hooks)
hop.tmux          # TPM plugin entry point
```

## CLI Commands

See `cli.py:main()` for full command definitions.

```bash
uvx claude-tmux-hop <command>
  # Core commands
  register --state <s>    # Set state: waiting|idle|active
  clear                   # Remove hop state from pane
  cycle                   # Jump to next pane (priority order)
  back                    # Jump back to previous pane
  picker-data             # Output picker data for fzf
  switch <pane_id>        # Switch to specific pane (internal)
  list                    # Show all panes (auto-validates)
  discover                # Auto-discover Claude sessions
  prune                   # Remove stale panes
  status                  # Output status bar string

  # Management commands
  install                 # Install tmux/claude plugins
  update                  # Update installed plugins
  doctor                  # Check environment
  test [suite]            # Run self-tests
```

## Key Patterns

### State Priority
See `priority.py:STATE_PRIORITY`
- `waiting` (0): user input needed - oldest first
- `idle` (1): task complete - newest first
- `active` (2): running - newest first

### Tmux State Storage
See `tmux.py:set_pane_state()`, `get_hop_panes()`
- Uses custom pane options: `@hop-state`, `@hop-timestamp`

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
2. Call `handle_state_notifications(state, project, pane_context)`
3. If `@hop-focus-app` matches: focus terminal app → tab → tmux window → pane
4. If `@hop-notify` matches and terminal not focused: send OS notification

**Smart Suppression:**
- `is_terminal_focused()` checks if user is already looking at the terminal
- Skips notification if terminal (and correct tab on macOS) is focused

**Click-to-Focus (macOS only, optional):**
- Uses `terminal-notifier` if installed (external dep, not required)
- Falls back to AppleScript `display notification` (no click action)

### Hook Flow (hooks.json)
- SessionStart → idle
- UserPromptSubmit → active
- PreToolUse (AskUserQuestion|ExitPlanMode) → waiting
- PostToolUse → active (after user answers question or grants permission)
- Notification (permission_prompt|elicitation_dialog) → waiting
- Stop → idle
- SessionEnd → clear

## Code Conventions

- Use `uv run` instead of `python`
- Uses dataclasses with type hints
- Don't import under functions unless it's necessary
- Extract magic numbers and constants out of scopes
- Well-structured and clean codes are already descriptive without verbose comments
- When implementing sharable codes, check duplication and consider modularizing

