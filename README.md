# Claude Tmux Hop

Quickly hop between Claude Code sessions running in tmux panes.

## Features

- **Priority-based cycling**: Jump to panes waiting for input first, then idle, then active
- **Jump-back**: Return to previous pane across sessions/windows with Alt+Space
- **Auto-hop**: Optionally auto-switch to panes when they need attention
- **Auto-registration**: Claude Code hooks automatically track pane states
- **Auto-discovery**: Existing Claude Code sessions are detected on plugin load
- **In-memory state**: Uses tmux pane options - no files, auto-cleanup when panes close
- **Cross-session navigation**: Works across all tmux sessions
- **Status bar integration**: Show pane counts with customizable icons

## Requirements

- [uv](https://docs.astral.sh/uv/) (for `uvx` command)
- tmux 3.0+
- Python 3.10+
- Claude Code

## Installation

### 1. Install Claude Code Plugin

```bash
claude plugin marketplace add unsafe9/claude-tmux-hop
claude plugin install claude-tmux-hop
```

### 2. Install Tmux Plugin (via TPM)

Add to `~/.tmux.conf`:

```bash
set -g @plugin 'unsafe9/claude-tmux-hop'
```

Then press `prefix + I` to install.

That's it! The CLI runs via `uvx` automatically - no manual Python installation needed.

Any existing Claude Code sessions will be automatically discovered and registered as `idle` on plugin load.

## Usage

### Key Bindings

| Key | Action |
|-----|--------|
| `prefix + Space` | Cycle to next Claude Code pane |
| `prefix + C-Space` | Open picker menu |
| `Alt + Space` | Jump back to previous pane (no prefix) |

### Configuration

Add to `~/.tmux.conf`:

```bash
# Customize cycle key (default: Space)
set -g @hop-cycle-key 'Space'

# Customize picker key (default: C-Space)
set -g @hop-picker-key 'C-Space'

# Customize back key (default: M-Space, root binding - no prefix)
set -g @hop-back-key 'M-Space'

# Cycle mode (default: priority)
# - priority: cycle within highest-priority group only
# - flat: cycle through all panes in priority order
set -g @hop-cycle-mode 'priority'

# Auto-hop: automatically switch to panes when they enter specific states
# Disabled by default. Set to comma-separated states to enable.
set -g @hop-auto 'waiting'           # Auto-switch when a pane needs input
# set -g @hop-auto 'waiting,idle'    # Also switch when tasks complete

# Priority-only mode (default: on)
# Only auto-hop if no other pane has equal or higher priority
set -g @hop-auto-priority-only 'on'  # Don't hop if another pane is already waiting
# set -g @hop-auto-priority-only 'off'  # Always hop regardless of other panes

# Status bar integration - show pane counts in status bar
set -g status-right '#{E:@hop-status} | %H:%M'

# Status format (default: "{waiting:󰂜} {idle:󰄬}")
# Syntax: {state:icon} shows "icon count" when count > 0
set -g @hop-status-format '{waiting:󰂜} {idle:󰄬} {active:󰑮}'  # Include active
# set -g @hop-status-format '{waiting:W} {idle:I} {active:A}'  # ASCII icons
```

### CLI Commands

```bash
# List all Claude Code panes
uvx claude-tmux-hop list

# Cycle to next pane (usually via keybinding)
uvx claude-tmux-hop cycle

# Show picker menu
# Jump back to previous pane (cross-session/window)
uvx claude-tmux-hop back
uvx claude-tmux-hop picker

# Discover existing Claude Code sessions (runs automatically on plugin load)
uvx claude-tmux-hop discover

# Remove stale state from panes no longer running Claude Code
uvx claude-tmux-hop prune

# Output status for tmux status bar
uvx claude-tmux-hop status
```

## How It Works

### Pane States

| State | Trigger | Priority |
|-------|---------|----------|
| `waiting` | User input required | Highest |
| `idle` | Task complete | Medium |
| `active` | Working | Lowest |

### Cycling Behavior

Controlled by `@hop-cycle-mode` (default: `priority`):

**Priority mode** (`set -g @hop-cycle-mode 'priority'`):
1. If waiting panes exist, cycle only through waiting panes (oldest first)
2. If no waiting, cycle through idle panes (newest first)
3. If no idle, cycle through active panes (newest first)

**Flat mode** (`set -g @hop-cycle-mode 'flat'`):
- Cycle through all panes in priority order (waiting → idle → active)
- Within each priority level, sorted by timestamp

### State Storage

State is stored directly on tmux panes using custom options:
- `@hop-state`: Current state (waiting/idle/active)
- `@hop-timestamp`: Unix timestamp of last state change

Benefits:
- No external files
- State auto-deleted when pane closes
- Fast (in-memory)

## License

MIT
