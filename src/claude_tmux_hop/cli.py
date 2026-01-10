"""CLI entry point for claude-tmux-hop."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
from datetime import datetime

from . import __version__
from .log import log_cli_call, log_error, log_info
from .priority import STATE_PRIORITY, VALID_CYCLE_MODES, VALID_STATES, get_cycle_group, group_by_state, sort_all_panes
from .tmux import (
    clear_pane_state,
    get_claude_panes_by_process,
    get_current_pane,
    get_current_session_window,
    get_global_option,
    get_hop_panes,
    get_stale_panes,
    has_hop_state,
    is_in_tmux,
    run_tmux,
    set_global_option,
    set_pane_state,
    supports_popup,
    switch_to_pane,
)


def _escape_tmux_label(s: str) -> str:
    """Escape a string for use in tmux menu labels.

    Escapes special characters that tmux interprets in menu labels.
    """
    # Escape backslashes first, then other special chars
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("#", "\\#")
    return s


def _format_time_ago(timestamp: int) -> str:
    """Format a Unix timestamp as a human-readable time ago string.

    Args:
        timestamp: Unix timestamp (seconds since epoch)

    Returns:
        String like "5s", "5m", "2h", "1d", "3w"
    """
    import time

    if not timestamp:
        return "?"

    now = int(time.time())
    diff = now - timestamp

    if diff < 0:
        return "?"  # Future timestamp (shouldn't happen)

    if diff < 60:
        return f"{diff}s"
    elif diff < 3600:
        minutes = diff // 60
        return f"{minutes}m"
    elif diff < 86400:
        hours = diff // 3600
        return f"{hours}h"
    elif diff < 604800:
        days = diff // 86400
        return f"{days}d"
    else:
        weeks = diff // 604800
        return f"{weeks}w"


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
    auto_states = {s.strip().lower() for s in auto_states_str.split(",") if s.strip()}

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
        new_priority = STATE_PRIORITY.get(new_state, 2)

        for pane in panes:
            if pane.id == current_pane:
                continue  # Skip current pane
            pane_priority = STATE_PRIORITY.get(pane.state, 2)
            if pane_priority <= new_priority:
                # Another pane has equal or higher priority - don't auto-hop
                log_info(f"auto-hop: skipped, {pane.id} has priority {pane.state}")
                return False

    return True


def do_auto_hop() -> None:
    """Perform auto-hop to the current pane.

    Should be called from the pane that's changing state.
    """
    current_pane = os.environ.get("TMUX_PANE")
    if not current_pane:
        log_info("auto-hop: no TMUX_PANE, skipping")
        return

    success = switch_to_pane(current_pane)
    if success:
        log_info(f"auto-hop: switched to {current_pane}")
    else:
        log_error(f"auto-hop: failed to switch to {current_pane}")


def cmd_register(args: argparse.Namespace) -> int:
    """Register the current pane with a state."""
    log_cli_call("register", {"state": args.state})

    if not is_in_tmux():
        log_info(f"register: not in tmux, skipping")
        return 0

    set_pane_state(args.state)
    log_info(f"register: state set to {args.state}")

    # Check for auto-hop
    if should_auto_hop(args.state):
        do_auto_hop()

    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    """Clear the hop state from the current pane."""
    log_cli_call("clear")

    if not is_in_tmux():
        log_info("clear: not in tmux, skipping")
        return 0

    clear_pane_state()
    log_info("clear: state cleared")
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    """Cycle to the next pane in priority order."""
    log_cli_call("cycle", {"pane": args.pane} if args.pane else None)

    if not is_in_tmux():
        log_error("cycle: not in tmux")
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

    # Auto-prune stale panes silently
    for pane in get_stale_panes():
        clear_pane_state(pane.id)
        log_info(f"cycle: auto-pruned {pane.id}")

    panes = get_hop_panes(validate=False)  # Already pruned above
    if not panes:
        log_info("cycle: no panes found")
        run_tmux("display-message", "No Claude Code sessions found")
        return 0

    # Get the group to cycle through
    group = get_cycle_group(panes, mode=args.mode)
    if not group:
        log_info("cycle: no group found")
        run_tmux("display-message", "No Claude Code sessions found")
        return 0

    # Find current pane and select next
    # Prefer --pane arg (from tmux keybinding), fall back to get_current_pane()
    current = args.pane if args.pane else get_current_pane()
    ids = [p.id for p in group]

    try:
        idx = ids.index(current)
        next_idx = (idx + 1) % len(ids)
    except ValueError:
        # Current pane not in group, go to first
        next_idx = 0

    target = group[next_idx]
    project = os.path.basename(target.cwd) if target.cwd else "?"
    log_info(f"cycle → {target.session}:{target.window} {project} ({target.state})")
    switch_to_pane(target.id, target.session, target.window)
    return 0


def cmd_back(args: argparse.Namespace) -> int:
    """Jump back to the previous pane."""
    log_cli_call("back")

    if not is_in_tmux():
        log_error("back: not in tmux")
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

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


def _has_fzf() -> bool:
    """Check if fzf is available."""
    return shutil.which("fzf") is not None


def _get_hop_command_path() -> str:
    """Get the path to the claude-tmux-hop command.

    Respects @hop-dev-path for local development.
    """
    dev_path = get_global_option("@hop-dev-path", "")
    if dev_path:
        return f"uv run --project {shlex.quote(dev_path)} claude-tmux-hop"
    return "uvx claude-tmux-hop"


def _picker_popup() -> int:
    """Show picker using tmux popup with fzf."""
    cmd = _get_hop_command_path()

    # Build fzf command
    # --ansi: enable color codes
    # --reverse: show from top
    # --no-info: hide match count
    # --with-nth=1: only show first field (before tab)
    # The selected line's second field (pane_id) is used for switching
    fzf_cmd = (
        f"{cmd} picker-data | "
        f"fzf --ansi --reverse --no-info --with-nth=1 --delimiter='\t' "
        f"--header='Claude Sessions' --pointer='>' --prompt='' "
        f"--bind='enter:execute-silent({cmd} switch --pane {{2}})+abort'"
    )

    # Run in popup
    run_tmux(
        "display-popup",
        "-E",  # Close popup when command exits
        "-w", "50%",
        "-h", "50%",
        "-T", " Claude Sessions ",
        "bash", "-c", fzf_cmd,
    )

    return 0


def _picker_menu(panes: list) -> int:
    """Show picker using tmux display-menu (fallback)."""
    sorted_panes = sort_all_panes(panes)
    current_session, current_window = get_current_session_window()

    menu_items = []
    for pane in sorted_panes:
        icon = {"waiting": "󰂜", "idle": "󰄬", "active": "󰑮"}.get(pane.state, "?")
        project = os.path.basename(pane.cwd) if pane.cwd else "unknown"
        time_ago = _format_time_ago(pane.timestamp)

        safe_project = _escape_tmux_label(project)
        safe_session = _escape_tmux_label(pane.session)

        label = f"{icon} {safe_project} ({safe_session}:{pane.window}) [{time_ago}]"

        if pane.session != current_session:
            target = f"{pane.session}:{pane.window}"
            cmd = f"switch-client -t {shlex.quote(target)} ; select-pane -t {shlex.quote(pane.id)}"
        elif pane.window != current_window:
            target = f"{pane.session}:{pane.window}"
            cmd = f"select-window -t {shlex.quote(target)} ; select-pane -t {shlex.quote(pane.id)}"
        else:
            cmd = f"select-pane -t {shlex.quote(pane.id)}"

        menu_items.append(label)
        menu_items.append("")  # Key shortcut (empty = none)
        menu_items.append(cmd)

    run_tmux(
        "display-menu",
        "-T",
        "#[align=centre]Claude Sessions",
        *menu_items,
    )
    return 0


def cmd_picker(args: argparse.Namespace) -> int:
    """Show a picker menu for all Claude Code panes."""
    log_cli_call("picker")

    if not is_in_tmux():
        log_error("picker: not in tmux")
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

    panes = get_hop_panes()
    if not panes:
        log_info("picker: no panes found")
        run_tmux("display-message", "No Claude Code sessions found")
        return 0

    log_info(f"picker: showing {len(panes)} panes")

    # Check for popup support and fzf availability
    use_popup = supports_popup() and _has_fzf() and not getattr(args, "menu", False)

    if use_popup:
        return _picker_popup()
    else:
        return _picker_menu(panes)


def cmd_picker_data(args: argparse.Namespace) -> int:
    """Output pane data for fzf picker (internal use).

    Outputs one line per pane: "icon project (session:window) [time]<TAB>pane_id"
    """
    if not is_in_tmux():
        return 1

    panes = get_hop_panes()
    if not panes:
        return 0

    sorted_panes = sort_all_panes(panes)

    for pane in sorted_panes:
        icon = {"waiting": "󰂜", "idle": "󰄬", "active": "󰑮"}.get(pane.state, "?")
        project = os.path.basename(pane.cwd) if pane.cwd else "unknown"
        time_ago = _format_time_ago(pane.timestamp)

        # Output: display_label<TAB>pane_id
        # fzf will show the label but we extract pane_id on selection
        label = f"{icon} {project} ({pane.session}:{pane.window}) [{time_ago}]"
        print(f"{label}\t{pane.id}")

    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    """Switch to a specific pane by ID (internal use for picker)."""
    if not is_in_tmux():
        return 1

    success = switch_to_pane(args.pane)
    return 0 if success else 1


def cmd_list(args: argparse.Namespace) -> int:
    """List all Claude Code panes with their state."""
    log_cli_call("list")

    if not is_in_tmux():
        log_error("list: not in tmux")
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

    panes = get_hop_panes()
    if not panes:
        log_info("list: no panes found")
        print("No Claude Code sessions found")
        return 0

    log_info(f"list: found {len(panes)} panes")
    sorted_panes = sort_all_panes(panes)

    for pane in sorted_panes:
        project = os.path.basename(pane.cwd) if pane.cwd else "unknown"
        ts = datetime.fromtimestamp(pane.timestamp).strftime("%H:%M:%S") if pane.timestamp else "——:——:——"
        print(f"{pane.state:8} {ts}  {pane.id:6} {pane.session}:{pane.window}  {project}")

    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    """Discover and register existing Claude Code sessions as idle."""
    log_cli_call("discover", {"dry_run": args.dry_run, "force": args.force})

    if not is_in_tmux():
        log_error("discover: not in tmux")
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

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
            log_info(f"discover: registered {pane_id} as idle")
            if not args.quiet:
                print(f"Registered: {pane_id} ({pane['session']}:{pane['window']}) - {project}")

        registered += 1

    if not args.dry_run and not args.quiet:
        print(f"\nDiscovered {registered} session(s)")
        if skipped > 0:
            print(f"Skipped {skipped} already registered session(s)")

    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Remove stale hop state from panes where Claude Code is no longer running."""
    log_cli_call("prune", {"dry_run": args.dry_run})

    if not is_in_tmux():
        log_error("prune: not in tmux")
        print("Error: Not running inside tmux", file=sys.stderr)
        return 1

    stale = get_stale_panes()

    if not stale:
        log_info("prune: no stale panes found")
        if not args.quiet:
            print("No stale panes found")
        return 0

    log_info(f"prune: found {len(stale)} stale panes")

    for pane in stale:
        project = os.path.basename(pane.cwd) if pane.cwd else "unknown"

        if args.dry_run:
            print(f"Would remove: {pane.id} ({pane.session}:{pane.window}) - {project}")
        else:
            clear_pane_state(pane.id)
            log_info(f"prune: cleared {pane.id}")
            if not args.quiet:
                print(f"Removed: {pane.id} ({pane.session}:{pane.window}) - {project}")

    if not args.dry_run and not args.quiet:
        print(f"\nPruned {len(stale)} stale pane(s)")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Output status bar string for tmux integration.

    Format string syntax (set via @hop-status-format):
        {state:icon} - shows "icon count" when count > 0, empty otherwise

    Example formats:
        "{waiting:󰂜} {idle:󰄬}"              - default, waiting + idle only
        "{waiting:󰂜} {idle:󰄬} {active:󰑮}"  - include active count
        "{waiting:W} {idle:I} {active:A}"    - ASCII icons
    """
    import re

    # Don't log to avoid overhead in polling scenario

    if not is_in_tmux():
        return 0  # Silent exit, no output

    # Get panes without validation for speed
    panes = get_hop_panes(validate=False)

    # Group by state
    groups = group_by_state(panes)
    counts = {
        "waiting": len(groups["waiting"]),
        "idle": len(groups["idle"]),
        "active": len(groups["active"]),
    }

    # Get format string from tmux option
    default_format = "{waiting:󰂜} {idle:󰄬}"
    format_str = get_global_option("@hop-status-format", default_format)

    # Parse and expand format: {state:icon} -> "icon count" or ""
    def expand_placeholder(match: re.Match) -> str:
        state = match.group(1)
        icon = match.group(2)
        count = counts.get(state, 0)
        return f"{icon} {count}" if count > 0 else ""

    result = re.sub(r"\{(\w+):([^}]*)\}", expand_placeholder, format_str)

    # Clean up multiple spaces and trim
    result = " ".join(result.split())

    if result:
        print(result, end="")
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="claude-tmux-hop",
        description="Hop between Claude Code sessions in tmux panes",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # register command
    register_parser = subparsers.add_parser(
        "register",
        help="Register current pane with a state",
    )
    register_parser.add_argument(
        "--state",
        "-s",
        required=True,
        choices=VALID_STATES,
        help="State to register",
    )
    register_parser.set_defaults(func=cmd_register)

    # clear command
    clear_parser = subparsers.add_parser(
        "clear",
        help="Clear hop state from current pane",
    )
    clear_parser.set_defaults(func=cmd_clear)

    # cycle command
    cycle_parser = subparsers.add_parser(
        "cycle",
        help="Cycle to next pane in priority order",
    )
    cycle_parser.add_argument(
        "--pane",
        "-p",
        help="Current pane ID (passed by tmux keybinding)",
    )
    cycle_parser.add_argument(
        "--mode",
        "-m",
        choices=VALID_CYCLE_MODES,
        default="priority",
        help="Cycle mode: 'priority' cycles within highest-priority group, 'flat' cycles through all panes",
    )
    cycle_parser.set_defaults(func=cmd_cycle)

    # back command
    back_parser = subparsers.add_parser(
        "back",
        help="Jump back to the previous pane",
    )
    back_parser.set_defaults(func=cmd_back)

    # picker command
    picker_parser = subparsers.add_parser(
        "picker",
        help="Show picker menu for Claude Code panes",
    )
    picker_parser.add_argument(
        "--menu",
        "-m",
        action="store_true",
        help="Force display-menu style (no popup)",
    )
    picker_parser.set_defaults(func=cmd_picker)

    # picker-data command (internal)
    picker_data_parser = subparsers.add_parser(
        "picker-data",
        help="Output pane data for fzf (internal)",
    )
    picker_data_parser.set_defaults(func=cmd_picker_data)

    # switch command (internal)
    switch_parser = subparsers.add_parser(
        "switch",
        help="Switch to a specific pane (internal)",
    )
    switch_parser.add_argument(
        "--pane",
        "-p",
        required=True,
        help="Pane ID to switch to",
    )
    switch_parser.set_defaults(func=cmd_switch)

    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List all Claude Code panes",
    )
    list_parser.set_defaults(func=cmd_list)

    # discover command
    discover_parser = subparsers.add_parser(
        "discover",
        help="Discover and register existing Claude Code sessions",
    )
    discover_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be registered without making changes",
    )
    discover_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Re-register panes that are already registered",
    )
    discover_parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output except errors",
    )
    discover_parser.set_defaults(func=cmd_discover)

    # prune command
    prune_parser = subparsers.add_parser(
        "prune",
        help="Remove stale hop state from panes no longer running Claude Code",
    )
    prune_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be removed without making changes",
    )
    prune_parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output except errors",
    )
    prune_parser.set_defaults(func=cmd_prune)

    # status command
    status_parser = subparsers.add_parser(
        "status",
        help="Output status for tmux status bar",
    )
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
