---
name: hop-status
description: List all Claude Code sessions currently tracked in tmux and their states (waiting/idle/active) at a glance. Use when the user asks about open Claude sessions across tmux — "현재 떠 있는 claude 세션", "tmux에 어떤 claude가 돌고 있어?", "어디서 입력 기다리고 있어?", "팬별 상태 보여줘", "list claude sessions", "which claude is waiting", "show hop status". Only useful inside a tmux session where the claude-tmux-hop plugin is installed.
---

# hop-status

Show the user a summary of every Claude Code pane tracked by `claude-tmux-hop`, grouped by state so they can see at a glance which sessions need attention.

## When to use

Trigger when the user asks about the current state of Claude Code sessions running across tmux panes/windows — typically because they want to know which session is waiting on input, which is still running, or where a finished session is sitting idle.

Do **not** trigger for general tmux pane listing, for switching panes (the plugin has its own `cycle`/`back` commands and an `i` keybinding for the picker), or when the user is not inside a tmux session.

## How to run

1. Confirm you are inside a tmux session. If `$TMUX` is unset, tell the user the skill only works inside tmux and stop.
2. Run `claude-tmux-hop list` via Bash. Output is one pane per line, whitespace-separated columns (`state` is padded to 8 chars, `pane_id` to 6, so split on runs of whitespace):
   ```
   <state>   <HH:MM:SS>  %<paneId>  <session>:<window>  <project>
   ```
   `<state>` is one of `waiting`, `idle`, `active`. The pane id always starts with `%` (tmux pane-id form). The timestamp is when the state was last set; if it shows `——:——:——`, the pane has no recorded state-change time — say "unknown" instead of trying to compute "ago". If output is `No Claude Code sessions found`, report that and stop.
3. Group rows by state in this priority order — **waiting → idle → active** — and within each group keep the order returned by the command (already sorted newest first).
4. Present a compact summary. Suggested format:

   ```
   waiting (N)
     - <project> · <session>:<window> · <pane> · <time-ago>
   idle (N)
     - …
   active (N)
     - …
   ```

   Convert the `HH:MM:SS` timestamp to a relative "N분 전" / "N min ago" using the user's language. Omit any group with zero entries.
5. If `waiting` panes exist, mention them first explicitly — those block the user. If none, say so plainly.

## Notes

- Do not invoke `cycle`, `switch`, `back`, or any pane-mutating command. This skill is read-only.
- Do not parse `picker-data` — its format is for fzf, not for humans.
- `list` already filters out panes where Claude Code is no longer running (it inspects live processes), and flips stale `waiting` panes to `idle` if their dialog is gone. You can trust the result without calling `prune`.
