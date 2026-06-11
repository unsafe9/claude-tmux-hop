---
name: hop-config
description: Inspect and persistently change `@hop-*` tmux options exposed by the `claude-tmux-hop` plugin (auto-hop, notifications, focus, keybindings, status format, cycle mode), AND update the plugin-managed conductor instructions in the workbench. Trigger whenever the user wants to view, enable, disable, or tune any claude-tmux-hop behavior — "auto-hop 켜줘", "waiting일 때만 알림 받게 해줘", "팝업 끄고 포커스만", "hop 키바인딩 바꿔", "@hop-auto 설정", "show hop config", "configure claude-tmux-hop", "turn on auto switching", "make it focus terminal when waiting", "change cycle key to ctrl-h" — or to update the conductor's instructions: "conductor instructions 업데이트", "conductor 정본 최신화", "conductor CLAUDE.md 갱신", "update conductor instructions". Both runtime (`tmux set-option -g`) AND on-disk (tmux.conf) must be updated so the change survives a tmux restart. Only relevant inside a tmux session with the plugin installed.
---

# hop-config

Manage `@hop-*` tmux options for the `claude-tmux-hop` plugin so the changes apply immediately AND survive a tmux restart.

The plugin reads its behavior from tmux options at startup (via `hop.tmux`) and at runtime (via the Python CLI). Setting an option only at runtime works until tmux restarts; writing it into `tmux.conf` works after restart but doesn't take effect now. The user always wants both, so this skill always does both.

## When to use

Trigger when the user asks to view or change anything the plugin documents as configurable — auto-hop targets, notification states, terminal focus, keybindings, status format, cycle mode, terminal app override. Korean phrasing is common ("켜줘", "꺼줘", "바꿔줘") — react to intent, not exact keywords.

Do **not** trigger when:
- The user is asking about session *state* across panes — that's the `hop-status` skill.
- The user is outside tmux (`$TMUX` unset). Tell them and stop.
- The plugin isn't installed (no `@hop-status` global option, no `claude-tmux-hop` binary on PATH). Suggest `claude-tmux-hop install`.

## Option catalog

These are the only options to touch. Everything else under `@hop-*` is internal runtime state (`@hop-state`, `@hop-timestamp`, `@hop-previous-pane`, `@hop-status`, `@hop-status-inbox`) and **must not** be edited — leave them alone.

| Option | Default | Valid values | What it does |
|---|---|---|---|
| `@hop-cycle-key` | `Space` | tmux key (e.g. `Space`, `C-h`, `M-j`) | Prefix-table key to cycle to next pane. |
| `@hop-picker-key` | `C-f` | tmux key | Prefix-table key to open the fzf picker. |
| `@hop-back-key` | `C-Space` | tmux key | Prefix-table key to jump back to the previous pane. |
| `@hop-inbox-key` | `i` | single key | Prefix-table key to open the notification inbox menu. |
| `@hop-cycle-mode` | `priority` | `priority` \| `flat` | Cycle order. `priority` groups by state (waiting → idle → active); `flat` cycles in tmux's pane order. |
| `@hop-auto` | (empty) | comma list of `waiting`, `idle`, `active` | States that auto-switch the user to the target pane. Empty = disabled. |
| `@hop-auto-priority-only` | `on` | `on` \| `off` | When `on`, suppress auto-hop if another pane is already at a higher-priority state. |
| `@hop-notify` | (empty) | comma list of states | States that fire an OS notification (toast). Empty = disabled. |
| `@hop-focus-app` | (empty) | comma list of states | States that bring the terminal app to the foreground and navigate to the pane's tab. Empty = disabled. |
| `@hop-terminal-app` | (auto-detected) | terminal app name (`iTerm`, `Ghostty`, `Alacritty`, …) | Override the auto-detected terminal. Only set if focus is targeting the wrong app. |
| `@hop-status-format` | `{waiting:󰂜} {idle:󰄬} {active:󰑮}` | format string with `{state:icon}` tokens, separated by spaces | Status-bar segment rendered by `#{E:@hop-status}`. Each token expands to `icon count` when count > 0, empty otherwise. Common tweak: drop `{active:󰑮}` to show attention states only. |
| `@hop-window-rename` | `off` | `on` \| `off` | Auto-rename the tmux window of each Claude pane to `<state-icon> <directory name>`, updated on every state change. State icons come from `@hop-status-format` tokens (fallback: built-in icons); with multiple Claude panes in one window the icon shows the highest-priority state among them. When the session ends, the directory name stays as the window label and only the icon is dropped. |
| `@hop-conductor-enabled` | `off` | `on` \| `1` \| `true` \| `yes` (any other value = off) | **Master toggle** for the Conductor feature. Off by default — when off, no conductor keybinding registers and `conductor --popup` refuses. Other primitives (`spawn-task`, `send-prompt`, `list --json`, `conductor --update-instructions`, `conductor --kill`) work regardless. Flipping this to `off` does **not** auto-tear-down a running conductor session; this skill's disable workflow chains a `conductor --kill` after the option flip. |
| `@hop-conductor-session` | `conductor` | tmux session name | Dual-purpose: (1) the tmux session the plugin spawns on first `prefix + y` and attaches the popup to, and (2) a filter source — any session with this name is excluded from cycle/picker/discover/inbox so the conductor itself never pollutes those flows. |
| `@hop-conductor-dir` | (empty → XDG default) | absolute path; supports `~` and `$VAR` expansion | Workbench directory `tmux new-session -c …` uses when spawning the conductor session. The plugin creates the dir if missing but does **not** seed `CLAUDE.md`; the SessionStart hook injects canon in-memory when the workbench has no `<conductor-instructions>` marker, and the user can persist it with `conductor --update-instructions`. If you change this option while a conductor session is alive, the running session keeps its current cwd — kill the session (`conductor --kill`) before the next popup to pick up the new dir. |
| `@hop-conductor-popup-key` | `y` | tmux key (prefix table) | Key to attach the popup to the conductor session. Creates the session on demand. `prefix + d` inside the popup detaches without killing claude. |
| `@hop-conductor-respawn-key` | `Y` | tmux key (prefix table) | Key to **respawn** the conductor: kills the existing session (if any) and re-attaches to a fresh claude. **Destructive** of any in-flight state. |

### Priority semantics for state lists

`waiting` > `idle` > `active`. State lists are processed by inclusion only; order in the comma list does not matter. Canonicalize on write: lowercase, deduplicate, drop unknown tokens. Empty string is the explicit "disabled" value (do not use `off`).

## Workflow

Follow these four steps in order. Do not skip step 3 (confirmation) when making changes — config edits are easy to revert but easier still to avoid.

### 1. Read current state

Run these in parallel:

```bash
tmux show-options -g | grep -E '^@hop-' || true
```

Parse the output. Strip surrounding quotes from values. Compare against the defaults table; mark each as `default` / `set: <value>`. Ignore internal options listed above.

If the user only asked to *see* the config, format a short table here and stop.

### 2. Find the active tmux.conf

Search in this order (mirrors `paths.py:get_active_tmux_config()`) and use the **first existing** file:

1. `$XDG_CONFIG_HOME/tmux/tmux.conf` (or `~/.config/tmux/tmux.conf` if `XDG_CONFIG_HOME` is unset)
2. `~/.tmux.conf`
3. `~/.tmux.conf.local` (oh-my-tmux)
4. `$XDG_CONFIG_HOME/tmux/tmux.conf.local`

```bash
for p in "${XDG_CONFIG_HOME:-$HOME/.config}/tmux/tmux.conf" "$HOME/.tmux.conf" "$HOME/.tmux.conf.local" "${XDG_CONFIG_HOME:-$HOME/.config}/tmux/tmux.conf.local"; do
  [ -e "$p" ] && { echo "$p"; readlink -f "$p"; break; }
done
```

If `readlink -f` differs from the original, the file is a symlink — usually a dotfiles repo. The user almost always wants the change committed there, so:
- Edit through the symlink path (the OS will write to the target). The `Edit` tool handles this transparently.
- Tell the user the resolved path and that the edit will land in their dotfiles repo, so they remember to commit/push.

If none of the four paths exist, do not silently create one. Ask the user where their tmux.conf is, or suggest running `claude-tmux-hop install` (which writes one).

### 3. Show the plan, get confirmation

Before editing, surface a one-screen plan:

```
config: ~/.config/tmux/tmux.conf  (→ /Users/foo/dotfiles/tmux.conf)
plan:
  @hop-auto: 'waiting' → 'waiting,idle'        (edit existing line)
  @hop-focus-app: (unset) → 'waiting'          (append new line)
runtime: tmux set-option -g for both
```

If the user already gave explicit, unambiguous instructions for a single change, you can proceed without an extra round-trip — but still print the plan before you edit, so they see what's about to happen.

### 4. Apply: file + runtime + targeted reload

For each option being changed, do these in order. **Before** editing anything, capture the OLD values for keybinding options — you'll need them to unbind cleanly in step C.

**A. Update the file** with the `Edit` tool.

Match any of these existing forms (whitespace-tolerant) and replace the whole line:

```
set  -g  @hop-X 'value'
set  -g  @hop-X "value"
set  -g  @hop-X value
set-option  -g  @hop-X 'value'
```

Canonical form to write:

```
set -g @hop-X 'value'
```

Use single quotes; escape any literal single quotes in the value by closing-and-reopening (`'\''`). Keep the existing line's leading indentation if any. Do not touch any other `@hop-X` line.

If no existing line matches, append the new line. Placement preference:
1. Just below an existing block of `set -g @hop-*` lines.
2. Otherwise, just below the `run '…claude-tmux-hop/hop.tmux'` (or `set -g @plugin '…/claude-tmux-hop'`) line.
3. Otherwise, at end of file.

**Never** add commentary like `# added by Claude` — keep the file clean.

**B. Update runtime:**

```bash
tmux set-option -g @hop-X 'value'
```

For removals (user says "disable", "off"), write empty string (e.g. `@hop-auto`, `@hop-notify`, `@hop-focus-app`) — that's how the plugin spells "off". For genuinely orthogonal options (keybindings, `@hop-cycle-mode`, `@hop-terminal-app`), `set -gu @hop-X` to truly unset, and delete the line from the file rather than leaving an empty-string assignment.

**C. Targeted reload — only when needed.**

Most options are read by the Python CLI on every call (`@hop-auto`, `@hop-auto-priority-only`, `@hop-notify`, `@hop-focus-app`, `@hop-terminal-app`, `@hop-status-format`, `@hop-window-rename`), so step B is enough — the change is live immediately and survives restart via step A.

But two categories of options are **baked into tmux bindings at plugin load time** by `hop.tmux:main()` and need a reload to take effect right now:

| Option | Why a reload is needed |
|---|---|
| `@hop-cycle-key`, `@hop-picker-key`, `@hop-back-key`, `@hop-inbox-key` | The key is hard-coded into a `tmux bind-key` call. Changing the option doesn't re-bind anything. |
| `@hop-cycle-mode` | The mode string is baked into the `cycle` shell command behind the cycle key. |
| `@hop-conductor-enabled`, `@hop-conductor-popup-key`, `@hop-conductor-respawn-key` | `hop.tmux` only binds the conductor keys when `enabled` is truthy at load time; flipping `enabled` (or changing either key) requires re-running it. When flipping enabled from `on` → `off`, two extra steps: (1) `tmux unbind-key` the old conductor keys explicitly — re-running `hop.tmux` no-ops in the disabled branch and won't clean up stale bindings; (2) run `claude-tmux-hop conductor --kill` to tear down the running session (idempotent — no-op if none). |

When any option from the table above changed, run this reload sequence:

```bash
# 1. Unbind the OLD keys you captured before editing (if a keybind changed).
#    Use the table below for the binding flavor.
tmux unbind-key '<old-cycle-key>'            # prefix table
tmux unbind-key '<old-picker-key>'           # prefix table
tmux unbind-key '<old-inbox-key>'            # prefix table
tmux unbind-key -n '<old-back-key>'          # root table — note -n

# 2. Re-run hop.tmux. This re-reads all options and re-binds with the new values,
#    and is safe to run repeatedly. It does NOT re-run the user's whole tmux.conf,
#    so unrelated config (status line, other plugins, other key tables) is untouched.
plugin_root=$(tmux show-environment -g TMUX_PLUGIN_MANAGER_PATH 2>/dev/null \
              | sed 's/^TMUX_PLUGIN_MANAGER_PATH=//' | sed "s|^~|$HOME|")
plugin_root="${plugin_root:-$HOME/.tmux/plugins}/claude-tmux-hop"
tmux run-shell "$plugin_root/hop.tmux"
```

Skip step 1 entirely if no keybinding option changed (e.g. `@hop-cycle-mode` flip alone). Skip the whole sequence if only Python-CLI-side options changed.

If the resolved `$plugin_root/hop.tmux` does not exist (user-local install, atypical TPM layout), fall back to telling the user to reload tmux manually (`tmux source-file <conf>`) and explain why — do not guess at paths.

**D. Verify:**

```bash
tmux show-options -g @hop-X
grep -n "@hop-X" <config-path>
tmux list-keys | grep -E "claude-tmux-hop|hop_picker|hop_inbox|<cmd-path>"  # only when a keybind changed
```

The first two should agree on the new value; the third should show bindings on the new keys and **no** binding on the old keys. Report what changed in one line per option.

## Validation rules

Reject and ask before writing if any of these hold:

- A state list contains a token that isn't `waiting`, `idle`, or `active`.
- A toggle option (`@hop-auto-priority-only`) is set to anything other than `on` / `off`.
- `@hop-cycle-mode` is set to anything other than `priority` / `flat`.
- A keybinding contains a space (tmux keys are single tokens).
- `@hop-status-format` has unbalanced braces or uses an unknown state token.

If the user's intent is ambiguous ("turn on notifications" — for which states?), default to `waiting` and say so in the plan. Waiting is almost always the right answer because it's the only state that actively blocks the user.

## Things to avoid

- **Do not edit pane-scoped state.** `@hop-state` and `@hop-timestamp` are set per-pane by the plugin's hooks; touching them corrupts the picker and inbox.
- **Do not run `tmux source-file`** unless the user asks. It re-runs the whole config and can have side effects elsewhere (status line resets, key tables re-bound, plugins re-init). The reload step in 4C uses `tmux run-shell '<plugin>/hop.tmux'` which is scoped to this plugin only — prefer that path.
- **Do not write a new tmux.conf from scratch.** If none exists, defer to `claude-tmux-hop install`.
- **Do not silently rewrite formatting** of unrelated lines. Edit the smallest possible region.
- **Do not git-commit or push the dotfiles repo.** Surface the resolved path and let the user commit themselves.

## Conductor instructions

Different surface from `@hop-*` options: the conductor's plugin-managed instructions live in a `<conductor-instructions>...</conductor-instructions>` block inside the workbench `CLAUDE.md` (`@hop-conductor-dir`, default `~/.config/claude-tmux-hop/conductor/`). Inside the block = plugin-owned. Outside the block = user-owned (preserved across updates).

**Trigger phrases**: "conductor instructions 업데이트", "conductor 정본 최신화", "conductor CLAUDE.md 갱신", "update conductor instructions", "refresh conductor template".

**Workflow**:

1. Run `claude-tmux-hop conductor --update-instructions`. Capture exit code + stdout + stderr.

2. Interpret the result:

   | stdout starts with | exit code | meaning |
   |---|---|---|
   | `wrote <path>` | 0 | First-time write into a workbench without a `CLAUDE.md`. |
   | `updated marker block in <path>` | 0 | Marker found and replaced; user content outside the marker preserved. |
   | `backed up existing file to <path>.bak` followed by `overwrote <path>` | 0 | `--force` path: existing non-marker file was backed up and overwritten. Only reached when the user explicitly opts into `--force`. |
   | stderr: conflict message (`exists but has no <conductor-instructions> marker`) | 1 | The workbench `CLAUDE.md` has no marker. Refuses by default. |

3. Report to the user in one line per outcome (verbatim stdout/stderr plus a short summary).

4. **Handling the conflict case** (exit 1):
   - Tell the user their `CLAUDE.md` has no marker, so the plugin cannot tell plugin-managed content from their customizations.
   - Offer two paths:
     - **Recommended**: the user edits the file themselves, wrapping any plugin-template-derived content in `<conductor-instructions>...</conductor-instructions>` tags (or removing it), then re-runs the command.
     - **`--force`**: the plugin backs up the entire file to `CLAUDE.md.bak` and overwrites with the bare template. User loses any customizations not preserved in `.bak`.
   - **Do NOT run `--force` without explicit user confirmation.** Wait for an explicit "yes, use `--force`" before retrying.

5. Pure verification commands (read-only, may run without confirmation): `cat <workbench>/CLAUDE.md`, `grep -n conductor-instructions <workbench>/CLAUDE.md` to show the current state.

**Out of scope**: this skill does not edit the workbench `CLAUDE.md` directly. All modification goes through `--update-instructions` so the marker semantics stay consistent. If the user wants free-form edits, point them at the file with the workbench path.
