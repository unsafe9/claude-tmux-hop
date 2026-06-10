# Claude Tmux Hop

Quickly hop between Claude Code sessions running in tmux panes.

## Features

- **Priority-based cycling**: Jump to panes waiting for input first, then idle, then active
- **Jump-back**: Return to previous pane across sessions/windows with Alt+Space
- **Auto-hop**: Optionally auto-switch to panes when they need attention
- **System notifications**: Display OS notification when panes need attention (macOS/Linux/Windows)
- **Terminal focus**: Automatically bring terminal to foreground when panes need attention (macOS/Linux/Windows)
- **Notification inbox**: Browse recent waiting/idle events in an fzf popup and jump to the pane
- **Auto-registration**: Claude Code hooks automatically track pane states, wait reasons, and task summaries
- **Auto-discovery**: Existing Claude Code sessions are detected on plugin load
- **In-memory state**: Pane state lives in tmux pane options - auto-cleanup when panes close
- **Cross-session navigation**: Works across all tmux sessions
- **Status bar integration**: Show pane counts with customizable icons
- **Popup picker**: Interactive fzf-based picker with time-in-state display (requires fzf)
- **Window auto-rename**: Optionally rename windows to `<state-icon> <directory name>` so state stays visible at a glance
- **Conductor** (opt-in): A persistent orchestrator Claude session in a popup that dispatches tasks to your other panes
- **Claude Code skills**: `hop-status` (session overview), `hop-config` (inspect/edit options), `hop-dispatch` (route a task to another pane)

## Requirements

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

Any existing Claude Code sessions will be automatically discovered and registered as `idle` on plugin load.

## Usage

### Key Bindings

| Key | Action |
|-----|--------|
| `prefix + Space` | Cycle to next Claude Code pane |
| `prefix + C-f` | Open picker menu |
| `prefix + i` | Open notification inbox (fzf popup, menu fallback) |
| `C-Space` | Jump back to previous pane (no prefix) |
| `prefix + y` | Open conductor popup (only when conductor is enabled) |
| `prefix + Y` | Respawn conductor and open popup (only when conductor is enabled) |

### Configuration

Add to `~/.tmux.conf`:

```bash
# Customize cycle key (default: Space)
set -g @hop-cycle-key 'Space'

# Customize picker key (default: C-f)
set -g @hop-picker-key 'C-f'

# Customize back key (default: C-Space, root binding - no prefix)
set -g @hop-back-key 'C-Space'

# Customize notification inbox key (default: i)
# Opens an fzf popup (display-menu fallback) listing recent waiting/idle state
# changes as aligned columns (icon, session:window, project, branch, time,
# wait reason, task); enter switches to that pane, ctrl-x clears the inbox.
set -g @hop-inbox-key 'i'

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

# Terminal app override (auto-detected from macOS bundle ID / TERM_PROGRAM by default)
# set -g @hop-terminal-app 'iTerm'   # Explicitly set terminal app name
# set -g @hop-terminal-app 'Ghostty' # Use this if tmux still detects Terminal.app

# Note: On macOS, iTerm2 and Terminal.app focus the specific tab/window
# containing the tmux session. Ghostty focuses the running app process without
# launching a new blank window.

# Status bar integration - show pane counts in status bar
set -g status-right '#{E:@hop-status} | %H:%M'

# Status format (default: "{waiting:󰂜} {idle:󰄬}")
# Syntax: {state:icon} shows "icon count" when count > 0
set -g @hop-status-format '{waiting:󰂜} {idle:󰄬} {active:󰑮}'  # Include active
# set -g @hop-status-format '{waiting:W} {idle:I} {active:A}'  # ASCII icons

# Window auto-rename (default: off)
# Renames the tmux window to "<state-icon> <directory name>" so the state icon
# stays current while the name remains a stable label (worktree directories
# naturally distinguish parallel work). State icons honor your
# @hop-status-format tokens. tmux automatic-rename is restored when the
# session ends.
set -g @hop-window-rename 'on'

# Conductor (default: off) - see the Conductor section below
set -g @hop-conductor-enabled 'on'
# set -g @hop-conductor-popup-key 'y'    # Attach conductor popup (prefix binding)
# set -g @hop-conductor-respawn-key 'Y'  # Kill + respawn conductor (prefix binding)
# set -g @hop-conductor-session 'conductor'  # Background session name
# set -g @hop-conductor-dir '~/.config/claude-tmux-hop/conductor/'  # Workbench dir
```

### CLI Commands

The CLI is bundled within each plugin and invoked automatically by tmux keybindings and Claude Code hooks. For debugging, invoke from the plugin directory:

```bash
# From tmux plugin directory (e.g., ~/.tmux/plugins/claude-tmux-hop)
./bin/claude-tmux-hop list      # List all Claude Code panes
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
1. If waiting panes exist, cycle only through waiting panes (newest first)
2. If no waiting, cycle through idle panes (newest first)
3. If no idle, cycle through active panes (newest first)

**Flat mode** (`set -g @hop-cycle-mode 'flat'`):
- Cycle through all panes in priority order (waiting → idle → active)
- Within each priority level, newest first

### State Storage

State is stored directly on tmux panes using custom options:
- `@hop-state`: Current state (waiting/idle/active)
- `@hop-timestamp`: Unix timestamp of last state change
- `@hop-task`: Task summary from Claude Code's session title, shown in picker/list/inbox
- `@hop-wait-reason`: Why a pane is waiting (question/plan/permission/elicitation), shown in picker/list/inbox

Benefits:
- No external files
- State auto-deleted when pane closes
- Fast (in-memory)

### Notification Inbox

`waiting` and `idle` state changes are recorded to
`~/.local/state/claude-tmux-hop/inbox.jsonl` so you can review what happened
while you were elsewhere. `prefix + i` opens an fzf popup (display-menu
fallback) listing recent entries as aligned columns — state icon,
session:window, project, branch, time ago, wait reason, task summary.
`enter` jumps to the pane, `ctrl-x` clears the inbox.

The inbox self-heals on open: entries whose pane is gone or no longer runs
Claude Code are pruned automatically, so killed panes never leave stale
notifications behind.

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

Identical notifications for the same pane are also deduplicated within a
120-second cooldown (e.g. repeated permission prompts in one turn), and the
notification body includes context when available: the permission message,
the pending question, or the task summary on completion.

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
- **App Focus**: AppleScript `activate` command, with System Events process focus for Ghostty
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
| 4 | `TERM_PROGRAM` | `vscode`, `Alacritty`, `ghostty`, etc. |

If macOS reports a Terminal.app bundle ID but `TERM_PROGRAM=ghostty`, Ghostty
is preferred to handle tmux sessions that were moved from Terminal.app.

Supported terminals include:
- **macOS**: Terminal.app, iTerm2, Alacritty, kitty, WezTerm, Ghostty, Hyper
- **IDEs**: VS Code, Cursor, Windsurf, Zed, Antigravity, all JetBrains IDEs
- **Linux**: gnome-terminal, Konsole, Alacritty, kitty, Tilix, Terminator
- **Windows**: Windows Terminal, ConEmu, Cmder

## Conductor (Opt-in)

The conductor is an orchestrator Claude Code session that lives in a
persistent background tmux session and dispatches work to your other panes.
Disabled by default:

```bash
set -g @hop-conductor-enabled 'on'
```

- `prefix + y` opens a popup attached to the conductor session (created on
  demand). `prefix + d` inside the popup detaches **without killing Claude** —
  anything in-flight keeps running in the background, and reopening the popup
  re-attaches to the same session.
- `prefix + Y` kills the conductor session first and attaches to a fresh
  Claude (destructive to in-flight work).
- The conductor sees a live snapshot of all tracked panes (state, project,
  branch, current task) on every prompt, and routes each task using one of
  four dispatch modes: switch to an existing pane, inject a prompt into one
  (`send-prompt`), spawn a new window in the project root (`spawn-task`), or
  spawn into a freshly created git worktree.
- The conductor works from a workbench directory (`@hop-conductor-dir`,
  default `~/.config/claude-tmux-hop/conductor/`). Its instructions are
  injected automatically; to persist them into the workbench `CLAUDE.md`
  (and customize around them), run the `hop-config` skill's "update conductor
  instructions" action.

The same dispatch modes are available outside the conductor via the
`hop-dispatch` skill in any Claude Code session — e.g. "spawn a fresh claude
on this in a worktree".

## Claude Code Skills

The plugin ships three skills, available in any Claude Code session:

| Skill | Purpose |
|-------|---------|
| `hop-status` | Summarize all tracked Claude sessions and their states |
| `hop-config` | Inspect and persistently edit `@hop-*` tmux options; update conductor instructions |
| `hop-dispatch` | Route a task to another Claude pane (switch / send-prompt / spawn-task / spawn-with-worktree) |

## License

MIT
