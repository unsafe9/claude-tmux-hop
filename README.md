# Claude Tmux Hop

Quickly hop between Claude Code sessions running in tmux panes.

## Features

- **Priority-based cycling**: Jump to panes waiting for input first, then idle, then active
- **Jump-back**: Return to previous pane across sessions/windows with Alt+Space
- **Auto-hop**: Optionally auto-switch to panes when they need attention
- **System notifications**: Display OS notification when panes need attention (macOS/Linux/Windows)
- **Terminal focus**: Automatically bring terminal to foreground when panes need attention (macOS/Linux/Windows)
- **Auto-registration**: Claude Code hooks automatically track pane states
- **Auto-discovery**: Existing Claude Code sessions are detected on plugin load
- **In-memory state**: Uses tmux pane options - no files, auto-cleanup when panes close
- **Cross-session navigation**: Works across all tmux sessions
- **Status bar integration**: Show pane counts with customizable icons
- **Popup picker**: Interactive fzf-based picker with time-in-state display (requires fzf)

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

# System notification: display OS notification when pane state changes
# Disabled by default. Set to comma-separated states to enable.
set -g @hop-notify 'waiting'         # Notify when a pane needs input
# set -g @hop-notify 'waiting,idle'  # Also notify when tasks complete

# Terminal focus: bring terminal app to foreground when pane state changes
# Disabled by default. Set to comma-separated states to enable.
set -g @hop-focus-app 'waiting'      # Focus terminal when a pane needs input

# Terminal app override (auto-detected from TERM_PROGRAM by default)
# set -g @hop-terminal-app 'iTerm'   # Explicitly set terminal app name

# Note: On macOS, iTerm2 and Terminal.app will focus the specific tab/window
# containing the tmux session, not just bring the app to foreground.

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

# Jump back to previous pane (cross-session/window)
uvx claude-tmux-hop back

# Show picker menu (fzf popup if available, else display-menu)
uvx claude-tmux-hop picker
uvx claude-tmux-hop picker --menu  # Force display-menu style

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

## Notifications & Focus

### Overview

Two complementary features help you stay aware of Claude Code activity:

| Feature | Option | Behavior |
|---------|--------|----------|
| **System Notification** | `@hop-notify` | Shows OS notification (toast) |
| **Terminal Focus** | `@hop-focus-app` | Brings terminal to foreground and navigates to pane |

### Smart Notification Suppression

Notifications are **automatically suppressed** when you're already looking at the terminal:

1. Checks if terminal app is the frontmost application
2. On macOS iTerm2/Terminal.app: also checks if the correct tab/window is focused
3. If already focused → no notification (avoids redundant alerts)

This prevents notification spam when you're actively working in the terminal.

### Auto-Focus Navigation

When `@hop-focus-app` is enabled, it performs **full navigation**:

```
Terminal App → Tab/Window → tmux Session → tmux Window → tmux Pane
```

| Platform | App Focus | Tab Focus | tmux Navigation |
|----------|-----------|-----------|-----------------|
| macOS | ✅ AppleScript | ✅ iTerm2, Terminal.app | ✅ |
| Linux | ✅ wmctrl/xdotool | ❌ | ✅ |
| Windows | ✅ PowerShell COM | ❌ | ✅ |

### Click-to-Focus Notifications (macOS)

When `@hop-focus-app` is **disabled** but `@hop-notify` is enabled, clicking the notification can navigate to the pane.

**Requires optional dependency:**
```bash
brew install terminal-notifier
```

| terminal-notifier | Notification | Click Action |
|-------------------|--------------|--------------|
| Not installed | ✅ Shows | ❌ Does nothing |
| Installed | ✅ Shows | ✅ Navigates to pane |

Without `terminal-notifier`, notifications still work - you just can't click them to navigate.

### Platform Support

#### macOS (Full Support)
- **Notifications**: Native via AppleScript `display notification`
- **Focus Detection**: AppleScript queries frontmost app and iTerm2/Terminal.app tabs
- **App Focus**: AppleScript `activate` command
- **Tab Focus**: AppleScript searches iTerm2 sessions / Terminal.app windows by tmux session name
- **Click-to-Focus**: Optional via `terminal-notifier`

#### Linux (X11)
- **Notifications**: `notify-send` (libnotify)
- **Focus Detection**: `xdotool getactivewindow` (X11 only, not Wayland)
- **App/Window Focus**: `wmctrl` or `xdotool`
- **Click-to-Focus**: Not supported (would require D-Bus complexity)

#### Windows
- **Notifications**: PowerShell Toast Notifications (Windows 10+)
- **Focus Detection**: PowerShell with Win32 `GetForegroundWindow`
- **App Focus**: PowerShell `WScript.Shell.AppActivate`
- **Click-to-Focus**: Not supported (would require protocol handler)

### Recommended Configuration

**Option A: Just notifications (minimal)**
```bash
set -g @hop-notify 'waiting'   # Alert when input needed
```

**Option B: Auto-focus (recommended)**
```bash
set -g @hop-focus-app 'waiting'   # Auto-navigate when input needed
```

**Option C: Both (belt and suspenders)**
```bash
set -g @hop-notify 'waiting'      # Alert if not focused
set -g @hop-focus-app 'waiting'   # Also auto-navigate
# Notification is suppressed when focus succeeds, so no duplicate alerts
```

### Terminal App Detection

The terminal app is auto-detected from environment variables:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | `@hop-terminal-app` option | User override |
| 2 | `__CFBundleIdentifier` (macOS) | `com.googlecode.iterm2` → iTerm |
| 3 | `WT_SESSION` (Windows) | Windows Terminal |
| 4 | `TERM_PROGRAM` | `vscode`, `Alacritty`, etc. |

Supported terminals include:
- **macOS**: Terminal.app, iTerm2, Alacritty, kitty, WezTerm, Ghostty, Hyper
- **IDEs**: VS Code, Cursor, Windsurf, Zed, Antigravity, all JetBrains IDEs
- **Linux**: gnome-terminal, Konsole, Alacritty, kitty, Tilix, Terminator
- **Windows**: Windows Terminal, ConEmu, Cmder

## License

MIT
