# Claude Tmux Hop

Quickly hop between Claude Code sessions running in tmux panes.

## Features

- **Priority-based cycling**: Jump to panes waiting for input first, then idle, then active
- **Jump-back**: Return to previous pane across sessions/windows with Alt+Space
- **Auto-hop**: Optionally auto-switch to panes when they need attention
- **System notifications**: Display OS notification when panes need attention (macOS/Linux/Windows)
- **Terminal focus**: Automatically bring terminal to foreground when panes need attention (macOS/Linux/Windows)
- **Notification inbox**: Browse panes waiting for input or sitting idle in an fzf popup and jump to them
- **Auto-registration**: Claude Code hooks automatically track pane states, wait reasons, and task summaries
- **Auto-discovery**: Existing Claude Code sessions are detected on plugin load
- **In-memory state**: Pane state lives in tmux pane options - auto-cleanup when panes close
- **Cross-session navigation**: Works across all tmux sessions
- **Status bar integration**: Show pane counts with customizable icons
- **Popup picker**: Interactive fzf-based picker with time-in-state display (requires fzf)
- **Window auto-rename**: Optionally rename windows to `<state-icon> <directory name>` so state stays visible at a glance
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
# Opens an fzf popup (display-menu fallback) listing all tracked panes
# (waiting/idle first, then active) as aligned columns (icon, session:window,
# project, branch, time, wait reason, task); enter switches to that pane,
# ctrl-x dismisses current notifications (active panes stay).
set -g @hop-inbox-key 'i'

# Inbox preview (default: off) - show the highlighted pane's live terminal
# content beside the list, so you can read what a pane is asking without
# hopping to it. ctrl-/ toggles it inside the popup, which also widens.
# set -g @hop-inbox-preview 'on'
# set -g @hop-inbox-preview-lines '40'  # Trailing lines of pane content shown

# Cycle mode (default: priority)
# - priority: cycle within highest-priority group only
# - flat: cycle through all panes in priority order
set -g @hop-cycle-mode 'priority'

# Cycle feedback (default: off)
# After each hop, flash your position in the queue plus the pane's project,
# wait reason, time in state, and task — e.g. "[2/3 waiting] palm-server ·
# permission · 4m · Refactor auth module".
# set -g @hop-cycle-feedback 'on'

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

# Status format (default: "{waiting:󰂜} {idle:󰄬} {active:󰑮}")
# Syntax: {state:icon} shows "icon count" when count > 0
# set -g @hop-status-format '{waiting:󰂜} {idle:󰄬}'            # Hide active
# set -g @hop-status-format '{waiting:W} {idle:I} {active:A}'  # ASCII icons

# Second status line (optional) - list the panes needing attention as
# background-colored "<state-icon> <directory name>" badges (waiting=yellow,
# idle=green), so you see which sessions are waiting without opening the inbox.
# With `mouse on`, clicking a badge jumps straight to that pane (it switches
# session, window, and pane via tmux's default status-click binding).
# Note: status 2 always reserves two rows, even when nothing is pending.
# set -g status 2
# set -g status-format[1] '#{E:@hop-status-inbox}'
#
# Badge colors are tmux style strings, overridable per state. Set to empty
# to disable coloring (plain text — the state icon still distinguishes them,
# and badges stay clickable).
# set -g @hop-status-inbox-waiting-style 'fg=colour235 bg=colour143'  # default
# set -g @hop-status-inbox-idle-style    'fg=colour235 bg=colour108'  # default
# set -g @hop-status-inbox-waiting-style ''   # disable color for waiting
# set -g @hop-status-inbox-idle-style    ''   # disable color for idle

# Up next (optional) - the single pane to deal with right now, as one badge
# (e.g. "󰂜 palm-server permission 4m"). Same ordering and dismiss filter as the
# inbox, so it always agrees with it; renders nothing when nothing is pending.
# Clicking it jumps to that pane, just like an inbox badge.
# set -g status-right '#{E:@hop-status-next} │ #{E:@hop-status} │ %H:%M'
#
# Label format (default: "{icon} {project} {reason} {age}")
# Tokens: {icon} {project} {branch} {reason} {age} {task}. Tokens with nothing
# to show collapse away, so panes without a branch or wait reason leave no gap.
# set -g @hop-status-next-format '{icon} {project} {branch} {age} {task}'
#
# The badge sits inline among unrelated status segments, so by default it
# takes the pane's state color as foreground and leaves the bar's background
# alone (unlike the second status line, where pills separate one pane from the
# next). Override with any tmux style, or empty to drop color entirely.
# set -g @hop-status-next-style 'fg=colour235 bg=colour110'  # pill instead
# set -g @hop-status-next-style ''   # no color (still clickable)

# Window auto-rename (default: off)
# Renames the tmux window to "<state-icon> <directory name>" so the state icon
# stays current while the name remains a stable label (worktree directories
# naturally distinguish parallel work). State icons honor your
# @hop-status-format tokens; with multiple Claude panes in one window the
# icon shows the highest-priority state among them. When a session ends, the
# directory name stays as the window label and only the icon is dropped.
set -g @hop-window-rename 'on'
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

Each hop flashes a line telling you where you landed, so you can tell how much
of the queue is left without opening the inbox:

```
[2/3 waiting] palm-server · permission · 4m · Refactor auth module
```

The counter is scoped to the panes actually being cycled — the current
priority group in priority mode, the whole pending list in flat mode. Enable
it with `set -g @hop-cycle-feedback 'on'`.

### State Storage

State is stored directly on tmux panes using custom options:
- `@hop-state`: Current state (waiting/idle/active)
- `@hop-timestamp`: Unix timestamp of last state change
- `@hop-task`: Task summary from Claude Code's session title, shown in picker/list/inbox
- `@hop-wait-reason`: Why a pane is waiting (question/plan/permission/elicitation), shown in picker/list/inbox
- `@hop-project` / `@hop-branch`: Git identity (main-repo name, branch), shown in the inbox

Benefits:
- No external files
- State auto-deleted when pane closes
- Fast (in-memory)
- Single source of truth: status bar, picker, cycle, and inbox all derive
  from the same pane options, so they can never disagree

### Notification Inbox

`prefix + i` opens an fzf popup (display-menu fallback) listing every tracked
Claude pane — attention first (waiting → idle), then active — as aligned
columns: state icon, session:window, project, branch, time ago, wait reason,
task summary. `enter` jumps to the pane, `ctrl-x` dismisses the waiting/idle
entries (they resurface on their next state change; active panes are an
overview, not notifications, so they stay; status bar counts are unaffected).

A preview window on the right shows the highlighted pane's live terminal
content — the question it is asking, the permission prompt it is blocked on,
the output it just finished — in the pane's own colors, so the whole queue can
be triaged without hopping. `ctrl-/` toggles it. A one-line summary heads the
preview (state icon, project, branch, age, wait reason, task), followed by the
last `@hop-inbox-preview-lines` lines (default 40) of actual content — the
blank padding tmux reports below a pane's last output is skipped. The preview
is opt-in via `@hop-inbox-preview 'on'`; without it the popup stays compact and
list-only. The display-menu fallback (no fzf, or tmux < 3.2) has no preview.

The listing is derived live from pane state, so it can't go stale: a killed
pane disappears with its options, and a force-killed Claude process gets its
leftover pane state cleaned up when the inbox opens.

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

## Claude Code Skills

The plugin ships three skills, available in any Claude Code session:

| Skill | Purpose |
|-------|---------|
| `hop-status` | Summarize all tracked Claude sessions and their states |
| `hop-config` | Inspect and persistently edit `@hop-*` tmux options |
| `hop-dispatch` | Route a task to another Claude pane (switch / send-prompt / spawn-task / spawn-with-worktree) |

## License

MIT
