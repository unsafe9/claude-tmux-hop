# Claude Tmux Hop - Claude Code Plugin

This is the Claude Code plugin component of claude-tmux-hop.

## Installation

```bash
claude plugin install unsafe9/claude-tmux-hop#main:claude-plugin
```

## What It Does

This plugin registers hooks that track the state of Claude Code sessions:

| Hook | State | When |
|------|-------|------|
| `UserPromptSubmit` | `active` | User submits a prompt |
| `Notification` (permission_prompt) | `waiting` | Claude needs input |
| `Stop` | `idle` | Claude finishes a task |
| `SessionEnd` | (cleared) | Claude Code exits |

## Prerequisites

The `claude-tmux-hop` CLI must be installed and available in PATH:

```bash
pip install claude-tmux-hop
```

## Manual Installation

If not using the plugin registry, copy the `.claude-plugin` directory to your Claude Code plugins location.
