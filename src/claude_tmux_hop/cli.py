"""CLI entry point for claude-tmux-hop."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime

from pathlib import Path

from .log import log_cli_call, log_error, log_info
from .notify import PaneContext, handle_state_notifications
from .parser import create_parser
from .priority import STATE_PRIORITY, get_cycle_group, group_by_state, sort_all_panes
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
    parse_state_set,
    run_tmux,
    set_pane_state,
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
DEFAULT_STATUS_FORMAT = "{waiting:󰂜} {idle:󰄬}"


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
                # Another pane has strictly higher priority - don't auto-hop
                log_info(f"auto-hop: skipped, {pane.id} has higher priority {pane.state}")
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
        session, window = get_current_session_window()
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
    log_cli_call("register", {"state": args.state})
    set_pane_state(args.state)
    log_info(f"register: state set to {args.state}")

    # Get project name for notifications
    project = os.path.basename(os.getcwd())

    # Build pane context for notifications and focus
    pane_context = _build_pane_context(project)

    # Handle notifications and terminal focus
    handle_state_notifications(args.state, project, pane_context)

    # Check for auto-hop
    if should_auto_hop(args.state):
        do_auto_hop()

    return 0


@requires_tmux(silent=True)
def cmd_clear(args: argparse.Namespace) -> int:
    """Clear the hop state from the current pane."""
    log_cli_call("clear")
    clear_pane_state()
    log_info("clear: state cleared")
    return 0


@requires_tmux(silent=False)
def cmd_cycle(args: argparse.Namespace) -> int:
    """Cycle to the next pane in priority order."""
    log_cli_call("cycle", {"pane": args.pane} if args.pane else None)

    # Auto-prune stale panes silently
    for pane in get_stale_panes():
        clear_pane_state(pane.id)
        log_info(f"cycle: auto-pruned {pane.id}")

    panes = get_hop_panes(validate=False)  # Already pruned above
    validate_waiting_panes(panes)
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
    log_info(f"cycle → {target.session}:{target.window} {target.project} ({target.state})")
    switch_to_pane(target.id, target.session, target.window)
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
        print(f"{label}\t{pane.id}")

    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    """Switch to a specific pane by ID (internal use for picker)."""
    if not is_in_tmux():
        return 1

    success = switch_to_pane(args.pane)
    return 0 if success else 1


@requires_tmux(silent=False)
def cmd_list(args: argparse.Namespace) -> int:
    """List all Claude Code panes with their state."""
    log_cli_call("list")
    panes = get_hop_panes()
    validate_waiting_panes(panes)
    if not panes:
        log_info("list: no panes found")
        print("No Claude Code sessions found")
        return 0

    log_info(f"list: found {len(panes)} panes")
    sorted_panes = sort_all_panes(panes)

    for pane in sorted_panes:
        ts = datetime.fromtimestamp(pane.timestamp).strftime("%H:%M:%S") if pane.timestamp else "——:——:——"
        print(f"{pane.state:8} {ts}  {pane.id:6} {pane.session}:{pane.window}  {pane.project}")

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
        "{waiting:󰂜} {idle:󰄬}"              - default, waiting + idle only
        "{waiting:󰂜} {idle:󰄬} {active:󰑮}"  - include active count
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

    result = re.sub(r"\{(\w+):([^}]*)\}", expand_placeholder, format_str)

    # Clean up multiple spaces and trim
    result = " ".join(result.split())

    if result:
        print(result, end="")
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
            print("  Not installed. Run: uvx claude-tmux-hop install")
        print()

    # Update Claude Code plugin
    if args.component in ("all", "claude"):
        print("Claude Code Plugin:")
        if installed["claude_plugin"]:
            success = update_claude_plugin() and success
        else:
            print("  Not installed. Run: uvx claude-tmux-hop install")
        print()

    if success:
        print("Update complete!")
        print("\nNext steps:")
        print("  1. Reload tmux config: tmux source ~/.tmux.conf")
        print("  2. Restart Claude Code sessions to apply changes")
    else:
        print("Update completed with warnings. Check messages above.")

    return 0 if success else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check environment and dependencies."""
    from .doctor import format_results, run_all_checks

    log_cli_call("doctor", {"json": args.json})

    results = run_all_checks()

    if args.json:
        print(format_results(results, use_json=True))
    else:
        print("Environment Check\n")
        print(format_results(results))
        print()

        # Summary
        required_failed = [r for r in results if not r.ok and r.required]
        if required_failed:
            print(f"FAIL: {len(required_failed)} required check(s) failed")
            return 1
        else:
            print("OK: All required checks passed")

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
        cmd_install=cmd_install,
        cmd_update=cmd_update,
        cmd_doctor=cmd_doctor,
    )
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
