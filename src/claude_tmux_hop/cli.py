"""CLI entry point for claude-tmux-hop."""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from . import __version__
from .log import log_cli_call, log_error, log_info
from .priority import VALID_STATES, get_cycle_group, sort_all_panes


def _escape_tmux_label(s: str) -> str:
    """Escape a string for use in tmux menu labels.

    Escapes special characters that tmux interprets in menu labels.
    """
    # Escape backslashes first, then other special chars
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("#", "\\#")
    return s


from .tmux import (
    clear_pane_state,
    get_claude_panes_by_process,
    get_current_pane,
    get_hop_panes,
    init_pane,
    is_claude_pane,
    is_in_tmux,
    run_tmux,
    set_pane_state,
    switch_to_pane,
)


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize the current pane as a Claude Code pane."""
    log_cli_call("init")

    if not is_in_tmux():
        log_info("init: not in tmux, skipping")
        return 0

    init_pane()
    log_info("init: pane initialized")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """Register the current pane with a state."""
    log_cli_call("register", {"state": args.state})

    if not is_in_tmux():
        log_info(f"register: not in tmux, skipping")
        return 0

    # Only register if this pane was initialized by Claude Code
    if not is_claude_pane():
        log_info(f"register: pane not initialized as claude pane, skipping")
        return 0

    set_pane_state(args.state)
    log_info(f"register: state set to {args.state}")
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

    panes = get_hop_panes()
    if not panes:
        log_info("cycle: no panes found")
        run_tmux("display-message", "No Claude Code sessions found")
        return 0

    # Get the group to cycle through
    group = get_cycle_group(panes)
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
    log_info(f"cycle: switching from {current} to {target.id} ({target.state})")
    switch_to_pane(target.id, target.session)
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

    # Sort panes for display
    sorted_panes = sort_all_panes(panes)

    # Get current session for cross-session switching
    current_session = run_tmux("display-message", "-p", "#{session_name}")

    # Build menu items
    menu_items = []
    for pane in sorted_panes:
        # State icon
        icon = {"waiting": "[W]", "idle": "[I]", "active": "[A]"}.get(pane.state, "[?]")

        # Project name from cwd
        project = os.path.basename(pane.cwd) if pane.cwd else "unknown"

        # Escape strings for tmux menu labels
        safe_project = _escape_tmux_label(project)
        safe_session = _escape_tmux_label(pane.session)

        # Menu entry: "icon project (session:window)"
        label = f"{icon} {safe_project} ({safe_session}:{pane.window})"

        # Command to switch to this pane (handle cross-session)
        # Use shlex.quote() for pane.id and pane.session in shell commands
        if pane.session != current_session:
            cmd = f"switch-client -t {shlex.quote(pane.session)} ; select-pane -t {shlex.quote(pane.id)}"
        else:
            cmd = f"select-pane -t {shlex.quote(pane.id)}"

        menu_items.append(label)
        menu_items.append("")  # Key shortcut (empty = none)
        menu_items.append(cmd)

    # Display menu
    run_tmux(
        "display-menu",
        "-T",
        "#[align=centre]Claude Sessions",
        *menu_items,
    )
    return 0


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

    from datetime import datetime

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
        if is_claude_pane(pane_id) and not args.force:
            skipped += 1
            continue

        project = os.path.basename(pane["cwd"]) if pane["cwd"] else "unknown"

        if args.dry_run:
            print(f"Would register: {pane_id} ({pane['session']}:{pane['window']}) - {project}")
        else:
            init_pane(pane_id)
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

    # init command (called by SessionStart hook)
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize current pane as Claude Code pane",
    )
    init_parser.set_defaults(func=cmd_init)

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
    cycle_parser.set_defaults(func=cmd_cycle)

    # picker command
    picker_parser = subparsers.add_parser(
        "picker",
        help="Show picker menu for Claude Code panes",
    )
    picker_parser.set_defaults(func=cmd_picker)

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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
