"""CLI entry point for claude-tmux-hop."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

from pathlib import Path

from .log import LOG_DIR, log_cli_call, log_debug, log_error, log_info
from .notify import PaneContext, clear_notification_stamp, handle_state_notifications
from .parser import create_parser
from .priority import (
    PENDING_STATES,
    STATE_PRIORITY,
    group_by_state,
    priority_sort_key,
    sort_all_panes,
)
from .tmux import (
    PaneInfo,
    _is_conductor_enabled,
    clear_pane_state,
    get_claude_panes_by_process,
    get_current_pane,
    get_current_session_window,
    get_git_context,
    get_git_identity,
    get_global_option,
    get_hop_panes,
    get_running_claude_pane_ids,
    get_stale_panes,
    get_window_states,
    has_hop_state,
    has_session,
    is_in_tmux,
    is_window_rename_enabled,
    kill_session_if_exists,
    parse_state_set,
    rename_window,
    resolve_conductor_dir,
    run_tmux,
    send_prompt_to_pane,
    set_global_option,
    set_pane_git_identity,
    set_pane_state,
    set_pane_task,
    spawn_conductor_session,
    spawn_window,
    switch_to_pane,
    validate_waiting_panes,
)

from functools import wraps
from typing import Callable


def requires_tmux(silent: bool = False) -> Callable:
    """Decorator that ensures command runs inside tmux.

    Args:
        silent: If True, exit silently with code 0. If False, print error and exit with code 1.
    """
    def decorator(func: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
        @wraps(func)
        def wrapper(args: argparse.Namespace) -> int:
            if not is_in_tmux():
                cmd_name = func.__name__.removeprefix("cmd_")
                params = {k: v for k, v in vars(args).items() if k not in ("func", "command")}
                log_cli_call(cmd_name, params or None)
                if silent:
                    log_info(f"{func.__name__}: not in tmux, skipping")
                    return 0
                else:
                    log_error(f"{func.__name__}: not in tmux")
                    print("Error: Not running inside tmux", file=sys.stderr)
                    return 1
            return func(args)
        return wrapper
    return decorator


# Time constants for formatting
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
SECONDS_PER_WEEK = 604800

# State icons for display
STATE_ICONS = {"waiting": "󰂜", "idle": "󰄬", "active": "󰑮"}

# Default status format string
DEFAULT_STATUS_FORMAT = "{waiting:󰂜} {idle:󰄬} {active:󰑮}"

# {state:icon} tokens in @hop-status-format — shared by the status bar
# renderer and the window-rename icon lookup.
STATUS_FORMAT_TOKEN_RE = re.compile(r"\{(\w+):([^}]*)\}")

# Task summary length limits
MAX_TASK_STORED = 200  # Maximum stored in the @hop-task tmux option
MAX_TASK_DISPLAY = 50  # Maximum shown in picker / list / hop-status
MAX_NOTIFY_DETAIL = 100  # Maximum detail length in OS notification body

# Inbox listing: per-column width caps (fzf truncates overflow at the popup
# edge, so these only bound how far the later columns get pushed right)
INBOX_COL_MAX = 56
INBOX_TASK_MAX = 100
INBOX_DISPLAY_LIMIT = 20

# Global option holding the inbox-clear dismiss stamp: pending panes whose
# timestamp predates it are hidden from the inbox and cycle until their
# state changes again. Dismissing is a view filter — pane state (and the
# status bar counts derived from it) is untouched.
INBOX_CLEARED_OPTION = "@hop-inbox-cleared-at"

# Pre-0.7 inbox entries lived in this jsonl; pane options are now the single
# source of truth, so any leftover file is deleted on the next inbox open.
LEGACY_INBOX_FILE = LOG_DIR / "inbox.jsonl"

# ANSI styles for the fzf inbox popup (--ansi)
ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[90m"
_ANSI_YELLOW = "\033[33m"
STATE_ANSI = {"waiting": _ANSI_YELLOW, "idle": "\033[32m", "active": "\033[36m"}
# Column order: icon, session:window, project, branch, time, reason, task.
# The icon column ("") is colored per-state via STATE_ANSI instead.
INBOX_COLUMN_STYLES = ("", "\033[35m", "", "\033[36m", _ANSI_DIM, _ANSI_YELLOW, "")

# How many bytes of the transcript tail to scan for the latest ai-title.
# Claude Code regenerates ai-title each user turn; the most recent one is
# always within the last few KB even on very long sessions. 64KB gives a
# generous safety margin while keeping the read cheap on every hook call.
TRANSCRIPT_TAIL_BYTES = 65536

# Leading code-fence / blockquote markers stripped when normalizing a task.
_TASK_PREFIX_RE = re.compile(r"^(```\S*|>+)\s*")
_TASK_WHITESPACE_RE = re.compile(r"\s+")


def _format_time_ago(timestamp: int) -> str:
    """Format a Unix timestamp as a human-readable time ago string.

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        String like "5s", "5m", "2h", "1d", "3w"
    """
    if not timestamp:
        return "?"

    now = int(time.time())
    diff = now - timestamp

    if diff < 0:
        return "?"  # Future timestamp (shouldn't happen)

    if diff < SECONDS_PER_MINUTE:
        return f"{diff}s"
    elif diff < SECONDS_PER_HOUR:
        minutes = diff // SECONDS_PER_MINUTE
        return f"{minutes}m"
    elif diff < SECONDS_PER_DAY:
        hours = diff // SECONDS_PER_HOUR
        return f"{hours}h"
    elif diff < SECONDS_PER_WEEK:
        days = diff // SECONDS_PER_DAY
        return f"{days}d"
    else:
        weeks = diff // SECONDS_PER_WEEK
        return f"{weeks}w"


def _get_state_icon(state: str) -> str:
    """Icon for a state, honoring user-configured `@hop-status-format` tokens.

    Falls back to STATE_ICONS when the format string has no token for the
    state (e.g. `active` with the default format).
    """
    format_str = get_global_option("@hop-status-format", DEFAULT_STATUS_FORMAT)
    icons = {m.group(1): m.group(2).strip() for m in STATUS_FORMAT_TOKEN_RE.finditer(format_str)}
    return icons.get(state) or STATE_ICONS.get(state, "")


def _best_window_state(states: list[str], fallback: str) -> str:
    """Pick the highest-priority state among a window's panes.

    With multiple claude panes in one window the icon surfaces the most
    attention-worthy state rather than the last event's. Unknown states are
    skipped; an empty or failed query falls back to the caller's own state.
    """
    known = [s for s in states if s in STATE_PRIORITY]
    return min(known, key=lambda s: STATE_PRIORITY[s]) if known else fallback


def _normalize_task(text: str) -> str:
    """Take the first substantive line, sanitize, and truncate to stored length.

    Lines that become empty after stripping code-fence / blockquote markers are
    skipped so prompts that open with a bare ``` fence fall through to the
    actual content. tmux option values cannot contain newlines, so all
    whitespace collapses to single spaces. The truncated form ends with an
    ellipsis when cut.
    """
    if not text:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = _TASK_PREFIX_RE.sub("", line)
        line = _TASK_WHITESPACE_RE.sub(" ", line).strip()
        if not line:
            continue
        if len(line) > MAX_TASK_STORED:
            line = line[: MAX_TASK_STORED - 1] + "…"
        return line
    return ""


def _format_task_display(task: str, max_len: int = MAX_TASK_DISPLAY) -> str:
    """Truncate task for display in picker / list / hop-status output."""
    if not task:
        return ""
    if len(task) > max_len:
        return task[: max_len - 1] + "…"
    return task


def _read_hook_stdin() -> dict | None:
    """Read hook payload JSON from stdin when stdin is piped.

    Claude Code pipes a JSON object to every hook command. When the binary is
    invoked manually (interactive shell), stdin is a tty and we skip reading.
    Returns None on any parse failure so callers can degrade gracefully.
    """
    try:
        if sys.stdin.isatty():
            return None
    except (ValueError, OSError):
        return None
    try:
        raw = sys.stdin.read()
    except (ValueError, OSError):
        return None
    if not raw or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log_debug("hook stdin: not valid JSON, ignoring")
        return None
    return payload if isinstance(payload, dict) else None


def _extract_task_from_transcript(path: str) -> str:
    """Read a Claude Code transcript jsonl tail and return the latest task summary.

    Priority:
      1. Most recent ``type == "ai-title"`` entry's ``aiTitle`` field
      2. Most recent ``type == "last-prompt"`` entry's ``lastPrompt`` field
      3. ""

    Reads only the tail of the file to keep this cheap on long sessions.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""

    lines = tail.splitlines()
    if size > TRANSCRIPT_TAIL_BYTES and lines:
        # Tail likely started mid-line; the first chunk may be a partial JSON record.
        lines = lines[1:]

    last_prompt = ""
    for line in reversed(lines):
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t == "ai-title":
            ai_title = d.get("aiTitle", "") or ""
            if ai_title:
                return _normalize_task(ai_title)
        elif t == "last-prompt" and not last_prompt:
            last_prompt = d.get("lastPrompt", "") or ""

    if last_prompt:
        return _normalize_task(last_prompt)
    return ""


def should_auto_hop(new_state: str) -> bool:
    """Check if auto-hop should be triggered for the given state.

    Args:
        new_state: The state being registered ("waiting", "idle", "active")

    Returns:
        True if auto-hop should be triggered
    """
    # Get auto-hop configuration
    auto_states_str = get_global_option("@hop-auto", "")
    if not auto_states_str:
        return False  # Disabled by default

    # Parse comma-separated states
    auto_states = parse_state_set(auto_states_str)

    # Check if new state triggers auto-hop
    if new_state not in auto_states:
        return False

    # Check priority-only flag (defaults to on)
    priority_only = get_global_option("@hop-auto-priority-only", "on").lower() != "off"

    if priority_only:
        # Get current pane ID from environment
        current_pane = os.environ.get("TMUX_PANE")
        if not current_pane:
            log_info("auto-hop: no TMUX_PANE, skipping priority check")
            return True

        # Get all panes and check if any other has equal or higher priority
        panes = get_hop_panes(validate=True)
        validate_waiting_panes(panes)
        new_priority = STATE_PRIORITY.get(new_state, 2)

        for pane in panes:
            if pane.id == current_pane:
                continue  # Skip current pane
            pane_priority = STATE_PRIORITY.get(pane.state, 2)
            if pane_priority < new_priority:
                # Strict < intentional: <= would deadlock equal-priority panes
                # (e.g. two "waiting" panes block each other from auto-hopping)
                log_info(f"auto-hop: skipped, {pane.id} has higher priority {pane.state}")
                return False

    return True


def do_auto_hop(pane_context: PaneContext | None = None) -> None:
    """Perform auto-hop to the target pane.

    Uses the full session+window+pane context when available so cross-session
    and cross-window jumps land on the exact pane. Falls back to the current
    TMUX_PANE when no context was resolved (e.g. tmux lookup failed).

    switch_to_pane is a no-op when the caller is already on the target pane,
    so running this unconditionally from hook handlers is safe.
    """
    if pane_context is not None:
        success = switch_to_pane(
            pane_context.pane_id,
            target_session=pane_context.session,
            target_window=pane_context.window,
        )
        if success:
            log_info(
                f"auto-hop: switched to {pane_context.session}:"
                f"{pane_context.window}.{pane_context.pane_id}"
            )
        else:
            log_error(f"auto-hop: failed to switch to {pane_context.pane_id}")
        return

    current_pane = os.environ.get("TMUX_PANE")
    if not current_pane:
        log_info("auto-hop: no TMUX_PANE, skipping")
        return

    success = switch_to_pane(current_pane)
    if success:
        log_info(f"auto-hop: switched to {current_pane}")
    else:
        log_error(f"auto-hop: failed to switch to {current_pane}")


def _build_pane_context(project: str) -> PaneContext | None:
    """Build PaneContext from current tmux environment.

    Args:
        project: Project name

    Returns:
        PaneContext if in tmux with valid pane, None otherwise
    """
    pane_id = os.environ.get("TMUX_PANE")
    if not pane_id:
        return None

    # Get session and window from tmux
    try:
        session, window = get_current_session_window(pane_id)
        if not session:
            return None

        return PaneContext(
            pane_id=pane_id,
            session=session,
            window=window if window is not None else 0,
            project=project,
        )
    except Exception:
        return None


@requires_tmux(silent=True)
def cmd_register(args: argparse.Namespace) -> int:
    """Register the current pane with a state."""
    reason = getattr(args, "reason", "") or ""
    log_cli_call("register", {"state": args.state, "reason": reason})
    set_pane_state(args.state, reason=reason)
    log_info(f"register: state set to {args.state}")

    # Hook payload is consumed once here: task resolution and the
    # notification detail both read from it.
    payload = _read_hook_stdin()

    # Refresh the per-pane task summary. Manual --task wins over the hook
    # payload so callers can override the auto-derived value when testing.
    task = _resolve_task_for_register(args, payload)
    if task:
        try:
            set_pane_task(task)
            log_info(f"register: task set ({len(task)} chars)")
        except RuntimeError as e:
            log_debug(f"register: set_pane_task failed: {e}")

    # Get project name for notifications
    project = os.path.basename(os.getcwd())

    # Window names are navigation labels: the dir basename is stable and short
    # (worktree dirs carry the task hint), while the ai-title churns every
    # turn. The task summary stays visible via inbox/picker and @hop-task.
    # The icon aggregates over the window's panes (own state was already
    # persisted above) so co-located agents can't mask a waiting sibling.
    if is_window_rename_enabled():
        icon = _get_state_icon(_best_window_state(get_window_states(), args.state))
        rename_window(f"{icon} {project}" if icon else project)

    # A new user turn resets notification dedup so the next waiting/idle
    # event for this pane always notifies fresh.
    if args.state == "active":
        clear_notification_stamp()

    # Build pane context for notifications and focus
    pane_context = _build_pane_context(project)

    # Store git identity as pane options so the inbox/cycle view can derive
    # everything from pane state. Resolved only for pending states — the
    # frequent active register skips the git call. The main-repo name (vs the
    # cwd basename) keeps worktree panes from duplicating the branch in both
    # the project and branch columns.
    if args.state in PENDING_STATES:
        branch, repo = get_git_identity(os.getcwd())
        set_pane_git_identity(repo, branch)

    # Focus and auto-hop are independent: app focus is an OS-level action,
    # tmux pane hopping is a tmux-level action. Both must be free to trigger
    # on the same event — e.g. user is already on the terminal but a
    # different pane needs to come to the front.
    detail = _notify_detail(args.state, payload, task)
    handle_state_notifications(args.state, project, pane_context, detail)

    if should_auto_hop(args.state):
        do_auto_hop(pane_context)

    return 0


def _resolve_task_for_register(args: argparse.Namespace, payload: dict | None) -> str:
    """Pick the task summary to store for this register call.

    Order: explicit --task arg > transcript ai-title/last-prompt from hook stdin > "".
    """
    override = getattr(args, "task", None)
    if override:
        return _normalize_task(override)

    if not payload:
        return ""
    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return ""
    return _extract_task_from_transcript(transcript_path)


def _notify_detail(state: str, payload: dict | None, task: str) -> str:
    """Pick the most informative one-liner for the OS notification body.

    waiting: the Notification hook's message (e.g. "Claude needs your
    permission to use Bash") or the pending AskUserQuestion text;
    idle: the task summary. "" falls back to the plain "project: state" body.
    """
    detail = ""
    if payload:
        event = payload.get("hook_event_name", "")
        if event == "Notification":
            message = payload.get("message")
            detail = message if isinstance(message, str) else ""
        elif event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
            tool_input = payload.get("tool_input")
            questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
            if isinstance(questions, list) and questions and isinstance(questions[0], dict):
                question = questions[0].get("question")
                detail = question if isinstance(question, str) else ""
    if not detail and state == "idle":
        detail = task
    return _format_task_display(_normalize_task(detail), MAX_NOTIFY_DETAIL)


@requires_tmux(silent=True)
def cmd_clear(args: argparse.Namespace) -> int:
    """Clear the hop state from the current pane."""
    log_cli_call("clear")
    clear_pane_state()

    # Keep the directory name as the window label once claude exits. State
    # was cleared above, so the query only sees surviving sibling agents —
    # their best-state icon stays; with none left the icon is dropped.
    if is_window_rename_enabled():
        project = os.path.basename(os.getcwd())
        icon = _get_state_icon(_best_window_state(get_window_states(), ""))
        rename_window(f"{icon} {project}" if icon else project)

    log_info("clear: state cleared")
    return 0


def _pending_panes(panes: list[PaneInfo]) -> list[PaneInfo]:
    """Panes pending the user's attention, in cycle order.

    Filters to PENDING_STATES, drops panes dismissed via inbox-clear (their
    timestamp predates the @hop-inbox-cleared-at stamp; a later state change
    resurfaces them), and sorts waiting → idle, newest first within each.
    """
    try:
        cleared_at = int(get_global_option(INBOX_CLEARED_OPTION, "0") or 0)
    except ValueError:
        cleared_at = 0
    pending = [
        p for p in panes
        if p.state in PENDING_STATES and p.timestamp > cleared_at
    ]
    pending.sort(key=lambda p: priority_sort_key(p.state, p.timestamp))
    return pending


@requires_tmux(silent=False)
def cmd_cycle(args: argparse.Namespace) -> int:
    """Cycle to the next pending pane (priority order)."""
    log_cli_call("cycle", {"pane": args.pane} if args.pane else None)

    hop_panes = get_hop_panes(validate=False)
    validate_waiting_panes(hop_panes)

    entries = _pending_panes(hop_panes)
    if not entries:
        log_info("cycle: no pending panes")
        run_tmux("display-message", "No notifications")
        return 0

    # In priority mode, only cycle within the top priority group
    if args.mode == "priority":
        top_state = entries[0].state
        entries = [e for e in entries if e.state == top_state]

    # Find current pane and select next
    # Prefer --pane arg (from tmux keybinding), fall back to get_current_pane()
    current = args.pane if args.pane else get_current_pane()
    ids = [e.id for e in entries]

    try:
        idx = ids.index(current)
        next_idx = (idx + 1) % len(ids)
    except ValueError:
        next_idx = 0

    for _ in range(len(entries)):
        target = entries[next_idx]
        if switch_to_pane(target.id, target.session, target.window):
            log_info(f"cycle → {target.project} ({target.state}) {target.id}")
            return 0
        # Pane vanished mid-cycle — its options died with it, just skip.
        log_info(f"cycle: skipped vanished {target.id}")
        entries.pop(next_idx)
        if not entries:
            break
        next_idx = next_idx % len(entries)

    run_tmux("display-message", "No notifications")
    return 0


@requires_tmux(silent=False)
def cmd_back(args: argparse.Namespace) -> int:
    """Jump back to the previous pane."""
    log_cli_call("back")

    # Get previous pane from global option
    previous_pane = get_global_option("@hop-previous-pane", "")
    if not previous_pane:
        log_info("back: no previous pane recorded")
        run_tmux("display-message", "No previous pane to jump to")
        return 0

    # Switch to previous pane (this will update @hop-previous-pane)
    success = switch_to_pane(previous_pane)
    if success:
        log_info(f"back: jumped to {previous_pane}")
    else:
        log_error(f"back: failed to switch to {previous_pane}")
        # Clear stale previous if pane no longer exists
        run_tmux("set-option", "-g", "-u", "@hop-previous-pane", check=False)
        run_tmux("display-message", "Previous pane no longer exists")
        return 1

    return 0


def cmd_picker_data(args: argparse.Namespace) -> int:
    """Output pane data for fzf picker (internal use).

    Outputs one line per pane: "icon project (session:window) [time]<TAB>pane_id"
    """
    if not is_in_tmux():
        return 1

    panes = get_hop_panes()
    validate_waiting_panes(panes)
    if not panes:
        return 0

    sorted_panes = sort_all_panes(panes)

    for pane in sorted_panes:
        icon = STATE_ICONS.get(pane.state, "?")
        time_ago = _format_time_ago(pane.timestamp)

        # Output: display_label<TAB>pane_id
        # fzf will show the label but we extract pane_id on selection
        label = f"{icon} {pane.project} ({pane.session}:{pane.window}) [{time_ago}]"
        if pane.wait_reason:
            label = f"{label} ({pane.wait_reason})"
        task = _format_task_display(pane.task)
        if task:
            label = f"{label}  {task}"
        print(f"{label}\t{pane.id}")

    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    """Switch to a specific pane by ID (internal use for picker)."""
    if not is_in_tmux():
        return 1

    success = switch_to_pane(args.pane)
    return 0 if success else 1


def _build_pane_records() -> list[dict]:
    """Build the JSON-shaped pane snapshot shared by `list --json` and the
    conductor's per-prompt context hook. Sort + waiting-staleness handling is
    already applied here so both callers get the same view.

    Callers are responsible for ensuring tmux is reachable — `get_hop_panes()`
    surfaces tmux errors naturally if it isn't.
    """
    panes = get_hop_panes()
    validate_waiting_panes(panes)
    sorted_panes = sort_all_panes(panes)
    return [
        {
            "id": pane.id,
            "state": pane.state,
            "timestamp": pane.timestamp,
            "cwd": pane.cwd,
            "session": pane.session,
            "window": pane.window,
            "project": pane.project,
            **get_git_context(pane.cwd),
            "task": pane.task,
            "wait_reason": pane.wait_reason,
        }
        for pane in sorted_panes
    ]


@requires_tmux(silent=False)
def cmd_list(args: argparse.Namespace) -> int:
    """List all Claude Code panes with their state."""
    log_cli_call("list", {"json": bool(getattr(args, "json", False))})

    if getattr(args, "json", False):
        print(json.dumps(_build_pane_records(), indent=2))
        return 0

    panes = get_hop_panes()
    validate_waiting_panes(panes)
    sorted_panes = sort_all_panes(panes)

    if not panes:
        log_info("list: no panes found")
        print("No Claude Code sessions found")
        return 0

    log_info(f"list: found {len(panes)} panes")

    for pane in sorted_panes:
        ts = datetime.fromtimestamp(pane.timestamp).strftime("%H:%M:%S") if pane.timestamp else "——:——:——"
        line = f"{pane.state:8} {ts}  {pane.id:6} {pane.session}:{pane.window}  {pane.project}"
        if pane.wait_reason:
            line = f"{line} [{pane.wait_reason}]"
        task = _format_task_display(pane.task)
        if task:
            line = f"{line}  — {task}"
        print(line)

    return 0


@requires_tmux(silent=False)
def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and register existing Claude Code sessions as idle."""
    log_cli_call("discover", {"dry_run": args.dry_run, "force": args.force})
    claude_panes = get_claude_panes_by_process()

    if not claude_panes:
        log_info("discover: no claude panes found by process")
        if not args.quiet:
            print("No Claude Code sessions found")
        return 0

    log_info(f"discover: found {len(claude_panes)} claude panes by process")
    registered = 0
    skipped = 0

    for pane in claude_panes:
        pane_id = pane["id"]

        # Skip already registered panes unless --force
        if has_hop_state(pane_id) and not args.force:
            skipped += 1
            continue

        project = os.path.basename(pane["cwd"]) if pane["cwd"] else "unknown"

        if args.dry_run:
            print(f"Would register: {pane_id} ({pane['session']}:{pane['window']}) - {project}")
        else:
            set_pane_state("idle", pane_id)
            # Idle panes surface in the inbox, so give them the same git
            # identity a real register would (rare one-shot path).
            branch, repo = get_git_identity(pane["cwd"])
            set_pane_git_identity(repo, branch, pane_id)
            log_info(f"discover: registered {pane_id} as idle")
            if not args.quiet:
                print(f"Registered: {pane_id} ({pane['session']}:{pane['window']}) - {project}")

        registered += 1

    if not args.dry_run and not args.quiet:
        print(f"\nDiscovered {registered} session(s)")
        if skipped > 0:
            print(f"Skipped {skipped} already registered session(s)")

    return 0


@requires_tmux(silent=False)
def cmd_prune(args: argparse.Namespace) -> int:
    """Remove stale hop state from panes where Claude Code is no longer running."""
    log_cli_call("prune", {"dry_run": args.dry_run})
    stale = get_stale_panes()

    if not stale:
        log_info("prune: no stale panes found")
        if not args.quiet:
            print("No stale panes found")
        return 0

    log_info(f"prune: found {len(stale)} stale panes")

    for pane in stale:
        if args.dry_run:
            print(f"Would remove: {pane.id} ({pane.session}:{pane.window}) - {pane.project}")
        else:
            clear_pane_state(pane.id)
            log_info(f"prune: cleared {pane.id}")
            if not args.quiet:
                print(f"Removed: {pane.id} ({pane.session}:{pane.window}) - {pane.project}")

    if not args.dry_run and not args.quiet:
        print(f"\nPruned {len(stale)} stale pane(s)")

    return 0


@requires_tmux(silent=True)
def cmd_status(args: argparse.Namespace) -> int:
    """Output status bar string for tmux integration.

    Format string syntax (set via @hop-status-format):
        {state:icon} - shows "icon count" when count > 0, empty otherwise

    Example formats:
        "{waiting:󰂜} {idle:󰄬} {active:󰑮}"  - default, all states
        "{waiting:󰂜} {idle:󰄬}"              - attention states only
        "{waiting:W} {idle:I} {active:A}"    - ASCII icons
    """
    # Don't log to avoid overhead in polling scenario

    # Get panes without validation for speed
    panes = get_hop_panes(validate=False)
    validate_waiting_panes(panes)

    # Group by state
    groups = group_by_state(panes)
    counts = {
        "waiting": len(groups["waiting"]),
        "idle": len(groups["idle"]),
        "active": len(groups["active"]),
    }

    # Get format string from tmux option
    default_format = DEFAULT_STATUS_FORMAT
    format_str = get_global_option("@hop-status-format", default_format)

    # Parse and expand format: {state:icon} -> "icon count" or ""
    def expand_placeholder(match: re.Match) -> str:
        state = match.group(1)
        icon = match.group(2)
        count = counts.get(state, 0)
        return f"{icon} {count}" if count > 0 else ""

    result = STATUS_FORMAT_TOKEN_RE.sub(expand_placeholder, format_str)

    # Clean up multiple spaces and trim
    result = " ".join(result.split())

    if result:
        print(result, end="")
    return 0


def _format_inbox_lines(entries: list[PaneInfo], use_ansi: bool = False) -> list[str]:
    """Render pending panes as aligned columns: "label<TAB>pane_id" lines.

    Columns (icon, session:window, project, branch, time, reason, task) are
    padded to the widest cell; columns empty across all entries are dropped.
    With use_ansi, cells are wrapped in INBOX_COLUMN_STYLES / STATE_ANSI for
    the fzf popup — display-menu callers must stay plain.
    """
    icons = {state: _get_state_icon(state) or "?" for state in {e.state for e in entries}}
    rows = [
        (
            icons[entry.state],
            f"{entry.session}:{entry.window}",
            _format_task_display(entry.repo or entry.project, INBOX_COL_MAX),
            _format_task_display(entry.branch, INBOX_COL_MAX),
            _format_time_ago(entry.timestamp),
            entry.wait_reason,
            _format_task_display(entry.task, INBOX_TASK_MAX),
        )
        for entry in entries
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(INBOX_COLUMN_STYLES))]

    lines = []
    for entry, row in zip(entries, rows):
        cells = []
        for i, cell in enumerate(row):
            if widths[i] == 0:
                continue
            padded = cell.ljust(widths[i])
            if use_ansi:
                style = STATE_ANSI.get(entry.state, "") if i == 0 else INBOX_COLUMN_STYLES[i]
                if style:
                    padded = f"{style}{padded}{ANSI_RESET}"
            cells.append(padded)
        label = "  ".join(cells).rstrip()
        lines.append(f"{label}\t{entry.id}")
    return lines


def cmd_inbox(args: argparse.Namespace) -> int:
    """Output all tracked panes for the fzf popup / display menu (internal use).

    The listing is derived straight from pane options (the single source of
    truth shared with the status bar and cycle), attention first: pending
    panes (waiting → idle, dismiss-stamp filtered) then active panes, newest
    first within each group. Active panes are an overview, not notifications —
    inbox-clear never hides them. Outputs one aligned line per pane; --ansi
    adds per-column colors for fzf.

    Hooks only fire on graceful exits, so a kill -9'd claude leaves stale
    state on its still-living pane. Opening the inbox is the natural
    validation point: such panes get their state cleared here, which also
    corrects the status bar counts. Gone panes need no handling — their
    options died with them.
    """
    LEGACY_INBOX_FILE.unlink(missing_ok=True)

    panes = get_hop_panes(validate=False)
    validate_waiting_panes(panes)

    running = get_running_claude_pane_ids()
    # A failed process scan (None) can't judge killed-claude panes —
    # show everything over mass-clearing live sessions.
    if running is not None:
        dead = [p for p in panes if p.id not in running]
        for pane in dead:
            clear_pane_state(pane.id)
        if dead:
            log_info(f"inbox: cleared {len(dead)} dead panes")
            panes = [p for p in panes if p.id in running]

    actives = sorted(
        (p for p in panes if p.state not in PENDING_STATES),
        key=lambda p: priority_sort_key(p.state, p.timestamp),
    )
    entries = (_pending_panes(panes) + actives)[:INBOX_DISPLAY_LIMIT]
    if not entries:
        return 0

    # Panes that haven't stored a git identity yet (pre-0.7 registers) show
    # fallback columns (cwd basename, blank branch); resolve and persist once
    # here so long-idle panes don't stay degraded until their next state change.
    for pane in entries:
        if not pane.repo and not pane.branch and pane.cwd:
            branch, repo = get_git_identity(pane.cwd)
            if repo or branch:
                set_pane_git_identity(repo, branch, pane.id)
                pane.repo, pane.branch = repo, branch

    for line in _format_inbox_lines(entries, use_ansi=bool(getattr(args, "ansi", False))):
        print(line)

    return 0


def cmd_inbox_clear(args: argparse.Namespace) -> int:
    """Dismiss current notifications via the cleared-at stamp.

    Pane state stays untouched — panes resurface in the inbox/cycle on
    their next state change.
    """
    set_global_option(INBOX_CLEARED_OPTION, str(int(time.time())))
    run_tmux("display-message", "Notifications cleared", check=False)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    """Interactive installation wizard."""
    from .install import (
        detect_environment,
        install_claude_plugin,
        install_tmux_plugin_manual,
        install_tmux_plugin_tpm,
        prompt_user,
    )

    log_cli_call("install", {"yes": args.yes, "component": args.component})

    print("Claude Tmux Hop Installation\n")

    # Detect environment
    print("Detecting environment...")
    env = detect_environment()

    print(f"  tmux: {'OK' if env['tmux']['installed'] else 'NOT FOUND'}")
    print(f"  claude: {'OK' if env['claude']['installed'] else 'NOT FOUND'}")
    print(f"  TPM: {'OK' if env['tpm']['installed'] else 'NOT FOUND'}")
    print(f"  fzf: {'OK' if env['fzf']['installed'] else 'NOT FOUND (optional)'}")
    print()

    if not env["tmux"]["installed"]:
        print("Error: tmux is required. Please install tmux first.")
        return 1

    success = True

    # Install tmux plugin
    if args.component in ("all", "tmux") and not args.skip_tmux:
        print("Tmux Plugin Installation")
        if args.yes or prompt_user("Install tmux plugin?"):
            if env["tpm"]["installed"]:
                if args.yes or prompt_user("  Use TPM (recommended)?"):
                    # Auto-detects config path (XDG, oh-my-tmux, traditional)
                    success = install_tmux_plugin_tpm() and success
                else:
                    # Auto-detects plugin directory
                    success = install_tmux_plugin_manual() and success
            else:
                print("  TPM not found. Installing manually...")
                # Auto-detects plugin directory
                success = install_tmux_plugin_manual() and success
        print()

    # Install Claude Code plugin
    if args.component in ("all", "claude") and not args.skip_claude:
        print("Claude Code Plugin Installation")
        if not env["claude"]["installed"]:
            print("  Skipping: Claude Code CLI not found")
        elif args.yes or prompt_user("Install Claude Code plugin?"):
            success = install_claude_plugin() and success
        print()

    # Summary
    if success:
        print("Installation complete!")
        print("\nNext steps:")
        print("  1. Reload tmux config (path shown above)")
        print("  2. If using TPM: press prefix + I to install")
        print("  3. Start a Claude Code session to test")
    else:
        print("Installation completed with warnings. Check messages above.")

    return 0 if success else 1


def cmd_update(args: argparse.Namespace) -> int:
    """Update installed plugins to latest version."""
    from .install import (
        update_claude_plugin,
        update_tmux_plugin,
        verify_installation,
    )

    log_cli_call("update", {"component": args.component})

    print("Claude Tmux Hop Update\n")

    # Check what's installed
    installed = verify_installation()
    success = True

    # Update tmux plugin
    if args.component in ("all", "tmux"):
        print("Tmux Plugin:")
        if installed["tmux_plugin"]:
            success = update_tmux_plugin() and success
        else:
            print("  Not installed. See README for installation instructions.")
        print()

    # Update Claude Code plugin
    if args.component in ("all", "claude"):
        print("Claude Code Plugin:")
        if installed["claude_plugin"]:
            success = update_claude_plugin() and success
        else:
            print("  Not installed. See README for installation instructions.")
        print()

    if success:
        print("Update complete!")
        print("\nNext steps:")
        print("  1. Reload tmux config: tmux source ~/.tmux.conf")
        print("  2. Restart Claude Code sessions to apply changes")
    else:
        print("Update completed with warnings. Check messages above.")

    return 0 if success else 1


@requires_tmux(silent=False)
def cmd_spawn_task(args: argparse.Namespace) -> int:
    """Open a new tmux window running claude with a pre-submitted prompt."""
    log_cli_call("spawn-task", {
        "session": args.session,
        "cwd": args.cwd,
        "switch": args.switch,
        "window_name": args.window_name or "",
    })

    cwd = Path(args.cwd).expanduser()
    if not cwd.is_dir():
        print(f"Error: cwd does not exist: {cwd}", file=sys.stderr)
        return 1

    window_id = spawn_window(
        session=args.session,
        cwd=str(cwd),
        prompt=args.prompt,
        window_name=args.window_name,
        switch=args.switch,
    )
    print(window_id)
    return 0


@requires_tmux(silent=False)
def cmd_send_prompt(args: argparse.Namespace) -> int:
    """Inject a prompt into an existing claude pane."""
    log_cli_call("send-prompt", {
        "pane": args.pane,
        "switch": args.switch,
        "force": args.force,
    })

    # Active-pane safety: refuse unless --force.
    pane_state = run_tmux(
        "show-option", "-pqv", "-t", args.pane, "@hop-state", check=False
    ).strip()
    if pane_state == "active" and not args.force:
        print(
            f"refusing: pane {args.pane} is active. use --force to override.",
            file=sys.stderr,
        )
        return 1

    # Verify the pane exists (display-message returns empty for unknown panes).
    probe = run_tmux(
        "display-message", "-p", "-t", args.pane, "#{pane_id}", check=False
    ).strip()
    if not probe:
        print(f"Error: pane {args.pane} not found", file=sys.stderr)
        return 1

    send_prompt_to_pane(args.pane, args.prompt, switch=args.switch)
    return 0


def cmd_conductor(args: argparse.Namespace) -> int:
    """Open the conductor popup, kill its session, or refresh the workbench CLAUDE.md.

    The popup attaches to a persistent detached `conductor` tmux session
    (created on demand). `prefix + d` inside the popup detaches without
    killing claude; re-attaching picks up the same claude with whatever
    progress it has made. `--respawn` rebuilds the session from scratch
    (destructive). `--kill` tears it down without opening a popup.
    """
    import shlex
    from .install import (
        ConductorInstructionsConflict,
        ensure_conductor_dir,
        update_conductor_instructions,
    )

    log_cli_call("conductor", {"mode": args.mode, "respawn": args.respawn, "force": args.force})
    conductor_dir = resolve_conductor_dir()

    if args.mode == "update_instructions":
        try:
            result = update_conductor_instructions(conductor_dir, force=args.force)
        except ConductorInstructionsConflict as e:
            print(str(e), file=sys.stderr)
            return 1
        if result.backup is not None:
            print(f"backed up existing file to {result.backup}")
        verb = {
            "created": "wrote",
            "replaced": "updated marker block in",
            "forced": "overwrote",
        }[result.action]
        print(f"{verb} {result.target}")
        return 0

    if not is_in_tmux():
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

    session = get_global_option("@hop-conductor-session", "conductor")

    if args.mode == "kill":
        killed = kill_session_if_exists(session)
        print(
            f"killed conductor session: {session}"
            if killed
            else f"no conductor session to kill: {session}"
        )
        return 0

    if not _is_conductor_enabled():
        print(
            "conductor disabled. enable via: tmux set -g @hop-conductor-enabled on",
            file=sys.stderr,
        )
        return 1

    ensure_conductor_dir(conductor_dir)

    if args.respawn:
        kill_session_if_exists(session)

    if not has_session(session):
        # Plugin-only users have our bin at ~/.claude/plugins/.../bin (not on
        # PATH), so the conductor's shell can't find `claude-tmux-hop` for
        # in-conductor tooling (hop-config skill, etc.). Prepend our bin dir
        # via tmux's session-scoped env — pip users already have it on PATH
        # and a redundant prepend is harmless. The `CLAUDE_TMUX_HOP_CONDUCTOR=1`
        # env var lets the SessionStart/UserPromptSubmit hooks short-circuit
        # for every claude in this session (the CLI handlers still do their
        # own cwd check as a second guard).
        own_bin = Path(sys.argv[0]).resolve().parent
        spawn_conductor_session(session, conductor_dir, own_bin)

    # display-popup -E propagates the inner shell's exit code; `tmux attach`
    # returns non-zero on certain detach paths. The popup itself launched
    # fine, so don't treat that as a tmux failure.
    attach_cmd = f"tmux attach -t {shlex.quote(session)}"
    run_tmux("display-popup", "-E", "-w", "80%", "-h", "80%", attach_cmd, check=False)
    return 0


def cmd_conductor_context(args: argparse.Namespace) -> int:
    """Emit SessionStart context JSON when cwd is the conductor workbench.

    Invoked by the SessionStart hook. Stays silent (exit 0, no output) for
    any cwd other than the conductor workbench, so it adds no overhead to
    regular Claude sessions. When the workbench is fresh or has no marker,
    injects the plugin-managed instructions via `additionalContext` (model
    only) and a `systemMessage` (user only) advising how to persist them.
    """
    from .install import (
        CONDUCTOR_INSTRUCTIONS,
        CONDUCTOR_MARKER_OPEN,
    )

    workbench = resolve_conductor_dir()
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        return 0

    try:
        workbench_resolved = workbench.resolve()
    except OSError:
        return 0

    if cwd != workbench_resolved:
        return 0

    claude_md = workbench / "CLAUDE.md"
    has_marker = False
    if claude_md.exists():
        try:
            has_marker = CONDUCTOR_MARKER_OPEN in claude_md.read_text()
        except OSError:
            has_marker = False
    if has_marker:
        return 0

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": CONDUCTOR_INSTRUCTIONS,
            "systemMessage": (
                "Conductor instructions injected in-memory for this session. "
                "To persist them in this workbench, exit and run: "
                "`claude-tmux-hop conductor --update-instructions`"
            ),
        }
    }
    print(json.dumps(payload))
    return 0


def cmd_conductor_prompt_context(args: argparse.Namespace) -> int:
    """Emit a fresh pane snapshot on each UserPromptSubmit in the conductor.

    Invoked by the UserPromptSubmit hook. Silent (exit 0, no output) outside
    the conductor workbench. Inside, injects the JSON pane snapshot as
    `additionalContext` so the conductor model can pick a dispatch target
    without needing a manual `list --json` round-trip every turn.

    The hook fires on every UserPromptSubmit across every Claude session in
    the user's environment — including ones outside tmux. Anything that
    might raise (missing tmux binary, unreadable options, OS errors during
    cwd lookup) is swallowed: a crashing hook would just spam the user's UI
    with no recovery path, and the silent path is functionally identical to
    "we couldn't tell you were the conductor."
    """
    try:
        workbench = resolve_conductor_dir().resolve()
        cwd = Path.cwd().resolve()
    except Exception:
        return 0

    if cwd != workbench:
        return 0

    try:
        records = _build_pane_records()
    except Exception:
        return 0

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Current Claude Code pane snapshot (auto-injected by the "
                "conductor's UserPromptSubmit hook; no need to run "
                "`hop-status` or `list --json` separately this turn):\n"
                + json.dumps(records, indent=2)
            ),
        }
    }
    print(json.dumps(payload))
    return 0


def main() -> int:
    """Main entry point."""
    parser = create_parser(
        cmd_register=cmd_register,
        cmd_clear=cmd_clear,
        cmd_cycle=cmd_cycle,
        cmd_back=cmd_back,
        cmd_picker_data=cmd_picker_data,
        cmd_switch=cmd_switch,
        cmd_list=cmd_list,
        cmd_discover=cmd_discover,
        cmd_prune=cmd_prune,
        cmd_status=cmd_status,
        cmd_inbox=cmd_inbox,
        cmd_inbox_clear=cmd_inbox_clear,
        cmd_install=cmd_install,
        cmd_update=cmd_update,
        cmd_spawn_task=cmd_spawn_task,
        cmd_send_prompt=cmd_send_prompt,
        cmd_conductor=cmd_conductor,
        cmd_conductor_context=cmd_conductor_context,
        cmd_conductor_prompt_context=cmd_conductor_prompt_context,
    )
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
