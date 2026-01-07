# Claude Tmux Hop

Quickly hop between Claude Code sessions running in tmux panes.

## Features

- **Priority-based cycling**: Jump to panes waiting for input first, then idle, then active
- **Auto-registration**: Claude Code hooks automatically track pane states
- **Auto-discovery**: Existing Claude Code sessions are detected on plugin load
- **In-memory state**: Uses tmux pane options - no files, auto-cleanup when panes close
- **Cross-session navigation**: Works across all tmux sessions

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
| `prefix + Tab` | Cycle to next Claude Code pane |
| `prefix + C-Tab` | Open picker menu (if enabled) |

### Configuration

Add to `~/.tmux.conf`:

```bash
# Customize cycle key (default: Tab)
set -g @hop-cycle-key 'Tab'

# Customize picker key (default: C-Tab)
set -g @hop-picker-key 'C-Tab'
```

### CLI Commands

```bash
# List all Claude Code panes
uvx claude-tmux-hop list

# Cycle to next pane (usually via keybinding)
uvx claude-tmux-hop cycle

# Show picker menu
uvx claude-tmux-hop picker

# Discover existing Claude Code sessions (runs automatically on plugin load)
uvx claude-tmux-hop discover

# Preview what would be discovered
uvx claude-tmux-hop discover --dry-run
```

## How It Works

### Pane States

| State | Trigger | Priority |
|-------|---------|----------|
| `waiting` | Notification hook (permission prompt) | Highest |
| `idle` | Stop hook (task complete) | Medium |
| `active` | UserPromptSubmit hook | Lowest |

### Cycling Behavior

1. If waiting panes exist, cycle only through waiting panes (oldest first)
2. If no waiting, cycle through idle panes (newest first)
3. If no idle, cycle through active panes (newest first)

### State Storage

State is stored directly on tmux panes using custom options:
- `@hop-state`: Current state (waiting/idle/active)
- `@hop-timestamp`: Unix timestamp of last state change

Benefits:
- No external files
- State auto-deleted when pane closes
- Fast (in-memory)

## Releasing a New Version

Update the version in `pyproject.toml`, then publish to PyPI:

```bash
uv build
uv publish
```

Both plugins use `uvx` which always fetches the latest version from PyPI.

## License

MIT
