---
name: hop-dispatch
description: Route a natural-language task to a Claude Code pane somewhere in tmux by picking one of four dispatch modes — navigate to an existing pane (`switch`), inject a follow-up prompt into one (`send-prompt`), spawn a new window in the project root (`spawn-task`), or spawn one in a freshly-created git worktree. Use whenever the user wants work to happen in a *different* Claude session than the current one — "이거 새 워크트리 따서 작업", "그 pane 한테 보내줘", "이 검토 다른 세션에서 굴려", "spawn a fresh claude on this", "send this to pane %X", "open a new window for it", "run it in a worktree". Also the surface the conductor popup uses for every dispatch. Only relevant inside a tmux session with the claude-tmux-hop plugin installed.
---

# hop-dispatch

Single entry point for routing a task to another Claude Code pane in the user's tmux. The skill picks one of four modes, confirms the plan with the user, and runs the matching `claude-tmux-hop` primitive (`switch`, `send-prompt`, or `spawn-task`). This skill is the **single source of truth** for those primitives' CLI shape — callers (the conductor popup, other agents, even direct user requests) should go through here rather than memorizing flags.

## When to use

Trigger when the user wants a task to happen *somewhere other than the current session* — they're describing routing intent, not asking you to do the work yourself. Examples:

- "그 검토 작업 새 워크트리 따서 따로 굴려" → mode (d)
- "이 follow-up은 메인 작업 pane에 보내" → mode (b)
- "지금 그 pane 좀 보여줘" / "switch me there" → mode (a)
- "한 번만 확인할 거니까 새 윈도우에 띄워줘" → mode (c)

You'll get triggered from two main contexts:

- **Conductor popup** (`prefix + y` / `prefix + Y`): a fresh pane snapshot is auto-injected at the top of every turn. Use it directly.
- **Any other Claude session**: no snapshot is pre-injected. Invoke the `hop-status` skill first (or `claude-tmux-hop list --json` directly) so you can see which panes/sessions exist before picking a target.

Do **not** trigger when:

- The user wants the work done *here* in the current session (just do the task).
- The user wants a status read-out with no routing intent (→ `hop-status` skill).
- The user wants to configure plugin options or refresh conductor instructions (→ `hop-config` skill).
- The user wants to navigate panes without a task ("그냥 cycle 좀") — they have the plugin's `cycle` / `back` / picker keybindings for that; routing through a dispatch skill is overkill.
- The plugin isn't installed (`claude-tmux-hop` not on PATH and no plugin bin reachable).

## The four dispatch modes

Pick exactly one. Defaults when ambiguous are in the table; otherwise ask.

| Mode | When to pick | Default trigger |
|---|---|---|
| **(a) Navigate** | A pane is already handling this exact task; the user just wants eyes on it. | Existing matching pane is `active`. |
| **(b) Inject follow-up** | An idle/waiting pane is on the right project and the task is a short follow-up that belongs in *that* session's context. | Existing matching pane is `idle` or `waiting` *and* the task is a follow-up, not a new direction. |
| **(c) New window, project root** | Quick query, investigation, code reading, one-shot — no branch isolation needed. | No matching pane *and* the task is read-only or short-lived. |
| **(d) New window, fresh worktree** | Feature work that will produce a branch/PR and shouldn't share a working tree with another task. | No matching pane *and* the task is multi-step feature work. |

"Matching pane" means same project (compare on the snapshot's `project` or `worktree_root`) — not just any Claude pane.

## How to run

1. **Get the pane snapshot.**
   - If the calling context auto-injected one (conductor popup), use it.
   - Otherwise, invoke the `hop-status` skill — its "Structured form" section will hand you the `list --json` shape with fields `id`, `state`, `cwd`, `session`, `window`, `project`, `branch`, `worktree_root`, `task`.

2. **Pick a mode** using the table above. Resolve the target:
   - For (a) and (b): pick the matching pane's `id` from the snapshot.
   - For (c) and (d): pick the target tmux `session` — usually the one that already owns the most Claude panes for the user. If the snapshot is empty, propose a session name and confirm.

3. **Show the plan in one screen and wait for confirmation** before executing. Format:
   ```
   Mode: <a/b/c/d>
   Target: <pane id> or <session>:<new-window>
   Cwd:   <worktree_root or new worktree path>   (modes c/d only)
   Prompt: <first user-facing prompt to send>
   ```
   Do not auto-execute. The user owns the trigger.

4. **Execute the matching command.** Signatures, sourced from `--help`:

   - **(a) Navigate:**
     ```
     claude-tmux-hop switch --pane <id>
     ```

   - **(b) Inject follow-up:**
     ```
     claude-tmux-hop send-prompt --pane <id> --prompt "<text>"
     ```
     The CLI **refuses `active` panes by default** — that's intentional, never inject into a running session. Only add `--force` if the user explicitly says so this turn; warn them it's disruptive.

   - **(c) New window, project root:**
     ```
     claude-tmux-hop spawn-task --session <session> --cwd <worktree_root> --prompt "<text>"
     ```
     Optional: `--window-name <name>`, `--no-switch` (stay where you are instead of switching to the new window).

   - **(d) New window, fresh worktree:**
     Run the worktree creation yourself first — `spawn-task` does **not** do it for you:
     ```
     git -C <repo_root> worktree add <new_worktree_path> -b <new_branch>
     claude-tmux-hop spawn-task --session <session> --cwd <new_worktree_path> --prompt "<text>"
     ```
     Pick `<new_worktree_path>` under the repo's `.claude/worktrees/` convention if the user has one; otherwise ask. Pick `<new_branch>` matching the user's branch-naming style if you can infer it from the snapshot's existing branches.

5. **First prompt** is the user's task verbatim, optionally prefixed with a slash-command (`/review-feature`, `/plan`, etc.) when obviously appropriate. Don't paraphrase the user's request away.

6. **Report the outcome** — what command ran, what the CLI said, where the user can find the new window/pane. Especially important for conductor popup callers: the popup closes after dispatch, so the user needs everything in one screen.

## Safety rules

- **Never `--force` `send-prompt`** unless the user explicitly tells you to override an active pane in this turn. For conductor popup callers, the popup closing after dispatch makes accidental injection irreversible from there.
- **Never dispatch silently.** Even if mode selection feels obvious, show the plan and let the user confirm. The cost of a wrong dispatch is a derailed Claude session somewhere else in tmux.
- **Never create a worktree for mode (d) without the branch name.** If you can't infer a branch from context, ask before running `git worktree add`.

## Notes

- The command shapes here track the plugin version this file ships with. If a flag here disagrees with `claude-tmux-hop <cmd> --help` at runtime, `--help` wins (you're likely running a different plugin version than this file was written for).
- This skill is intentionally the leaf node — it executes; it does not call other dispatch-class skills. The caller (conductor instructions, the user's own ask, etc.) is upstream.
