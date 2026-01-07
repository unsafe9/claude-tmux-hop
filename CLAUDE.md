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
claude-plugin/
  .claude-plugin/plugin.json  # 6 hook definitions
hop.tmux          # TPM plugin entry point
```

## CLI Commands

```bash
uvx claude-tmux-hop <command>
  init                    # Mark pane as Claude Code pane
  register --state <s>    # Set state: waiting|idle|active
  clear                   # Remove hop state from pane
  cycle                   # Jump to next pane (priority order)
  picker                  # Interactive menu
  list                    # Show all panes
  discover                # Auto-discover Claude sessions
```

## Key Patterns

### State Priority (priority.py)
- `waiting` (0): user input needed - oldest first
- `idle` (1): task complete - newest first
- `active` (2): running - newest first

### Tmux State Storage
Uses custom pane options: `@hop-claude`, `@hop-state`, `@hop-timestamp`

### Hook Flow (plugin.json)
- SessionStart → init
- UserPromptSubmit → active
- PreToolUse (AskUserQuestion|ExitPlanMode) → waiting
- Notification (permission_prompt|elicitation_dialog) → waiting
- Stop → idle
- SessionEnd → clear

### Code Conventions
- Functions: `cmd_<command>()` for CLI handlers
- Uses dataclasses with type hints
- Early return when not in tmux (`is_in_tmux()`)
- Subprocess calls for tmux commands with error handling

