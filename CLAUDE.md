# Claude Tmux Hop

A tool for navigating between multiple Claude Code sessions in tmux panes with priority-based cycling.

## Tech Stack

- Python 3.10+ (standard library only, no external deps)
- `uv`

## Deployment

- I'll create a github release with `version-bump` command
  - It will bump the pyproject version
  - PyPI publish will be automatically triggered by github workflow
- Update the version of `@claude-plugin/.claude-plugin/plugin.json` file when the plugin has changed
- Update the version of `@.claude-plugin/marketplace.json` file when the marketplace config has changed

## Project Structure

```
src/claude_tmux_hop/
  cli.py          # CLI entry (argparse subcommands)
  tmux.py         # Tmux operations, PaneInfo dataclass
  priority.py     # State priority logic
  log.py          # Logging to ~/.local/state/claude-tmux-hop/hop.log
  notify/         # Notification & focus module (Strategy pattern)
    __init__.py   # Public API, registries, terminal detection
    base.py       # Protocols (Notifier, FocusHandler, FocusDetector), PaneContext
    macos.py      # macOS: AppleScript notifications, focus, tab detection
    linux.py      # Linux: notify-send, wmctrl/xdotool, X11 detection
    windows.py    # Windows: PowerShell toast, COM focus, Win32 detection
hooks/
  hooks.json          # Hook definitions (7 hooks)
hop.tmux          # TPM plugin entry point
```

## CLI Commands

```bash
uvx claude-tmux-hop <command>
  register --state <s>    # Set state: waiting|idle|active
  clear                   # Remove hop state from pane
  cycle                   # Jump to next pane (priority order)
  picker                  # Interactive menu
  list                    # Show all panes (auto-validates)
  discover                # Auto-discover Claude sessions
  prune                   # Remove stale panes no longer running Claude
```

## Key Patterns

### State Priority (priority.py)
- `waiting` (0): user input needed - oldest first
- `idle` (1): task complete - newest first
- `active` (2): running - newest first

### Tmux State Storage
Uses custom pane options: `@hop-state`, `@hop-timestamp`

### Auto-Hop (cli.py)
Optional feature to auto-switch to panes on state change:
- `@hop-auto`: comma-separated states to trigger (default: empty = disabled)
- `@hop-auto-priority-only`: only hop if highest priority (default: "on")
- Implemented in `should_auto_hop()` and `do_auto_hop()`, called from `cmd_register()`

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

### Code Conventions
- Functions: `cmd_<command>()` for CLI handlers
- Uses dataclasses with type hints
- Early return when not in tmux (`is_in_tmux()`)
- Subprocess calls for tmux commands with error handling

## Tool Use
- Use `uv run` instead of `python`

