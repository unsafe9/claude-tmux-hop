"""Tmux operations for pane state management."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass

from .log import log_debug, log_error

# Pattern to detect Claude Code processes (shows as semver in pane_current_command)
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class PaneInfo:
    """Information about a tmux pane with hop state."""

    id: str  # e.g., "%99"
    state: str  # "waiting", "idle", "active"
    timestamp: int  # Unix timestamp
    cwd: str  # Current working directory
    session: str  # Session name
    window: int  # Window index


def run_tmux(*args: str, check: bool = True) -> str:
    """Run a tmux command and return stdout.

    Raises:
        RuntimeError: If check=True and the command fails
    """
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=check,
        )
        output = result.stdout.strip()
        # Log non-query commands (skip list-panes, show-option, display-message -p)
        cmd = args[0] if args else ""
        if cmd not in ("list-panes", "show-option", "display-message"):
            log_debug(f"tmux {' '.join(args[:3])}...")
        return output
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "No error message"
        log_error(f"tmux failed: {' '.join(args[:3])}... -> {stderr}")
        raise RuntimeError(
            f"tmux command failed: tmux {' '.join(args)}\nError: {stderr}"
        ) from e


def get_current_pane() -> str | None:
    """Get the current pane ID from tmux.

    Uses tmux display-message to get the active pane, which works correctly
    even when called from keybindings (where TMUX_PANE env var is not set).
    """
    try:
        result = run_tmux("display-message", "-p", "#{pane_id}")
        return result if result else None
    except RuntimeError:
        return None


def is_in_tmux() -> bool:
    """Check if we're running inside tmux."""
    return "TMUX" in os.environ


def _pane_target_args(pane_id: str | None) -> list[str]:
    """Return target args for pane option commands."""
    return ["-t", pane_id] if pane_id else []


def set_pane_state(state: str, pane_id: str | None = None) -> None:
    """Set the hop state for a pane.

    Args:
        state: The state to set ("waiting", "idle", "active")
        pane_id: The pane ID, or None for current pane
    """
    target = _pane_target_args(pane_id)
    timestamp = str(int(time.time()))
    run_tmux("set-option", "-p", *target, "@hop-state", state)
    run_tmux("set-option", "-p", *target, "@hop-timestamp", timestamp)


def init_pane(pane_id: str | None = None) -> None:
    """Initialize a pane as a Claude Code pane.

    Sets a marker to indicate this pane has an active Claude Code session.

    Args:
        pane_id: The pane ID, or None for current pane
    """
    target = _pane_target_args(pane_id)
    run_tmux("set-option", "-p", *target, "@hop-claude", "1")


def is_claude_pane(pane_id: str | None = None) -> bool:
    """Check if a pane is marked as a Claude Code pane.

    Args:
        pane_id: The pane ID, or None for current pane

    Returns:
        True if the pane has the Claude marker set
    """
    target = _pane_target_args(pane_id)
    try:
        result = run_tmux("show-option", "-p", *target, "-qv", "@hop-claude", check=False)
        return result == "1"
    except RuntimeError:
        return False


def clear_pane_state(pane_id: str | None = None) -> None:
    """Clear the hop state and Claude marker from a pane.

    Args:
        pane_id: The pane ID, or None for current pane
    """
    target = _pane_target_args(pane_id)
    run_tmux("set-option", "-p", *target, "-u", "@hop-claude", check=False)
    run_tmux("set-option", "-p", *target, "-u", "@hop-state", check=False)
    run_tmux("set-option", "-p", *target, "-u", "@hop-timestamp", check=False)


def get_running_claude_pane_ids() -> set[str]:
    """Get the set of pane IDs currently running Claude Code.

    Claude Code shows as a semver version (e.g., "2.0.76") in pane_current_command.

    Returns:
        Set of pane IDs (e.g., {"%0", "%5"}) running Claude Code.
    """
    output = run_tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\t#{pane_current_command}",
    )

    pane_ids = set()
    for line in output.split("\n"):
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 2:
            continue

        pane_id, command = parts
        if _SEMVER_PATTERN.match(command):
            pane_ids.add(pane_id)

    return pane_ids


def get_claude_panes_by_process() -> list[dict]:
    """Find all panes running Claude Code by checking process name.

    Claude Code shows as a semver version (e.g., "2.0.76") in pane_current_command.

    Returns:
        List of dicts with pane info for each Claude pane found.
    """
    output = run_tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{session_name}\t#{window_index}",
    )

    panes = []
    for line in output.split("\n"):
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 5:
            continue

        pane_id, command, cwd, session, window_str = parts

        if _SEMVER_PATTERN.match(command):
            panes.append({
                "id": pane_id,
                "command": command,
                "cwd": cwd,
                "session": session,
                "window": int(window_str) if window_str else 0,
            })

    return panes


def get_hop_panes(validate: bool = True) -> list[PaneInfo]:
    """Get all panes with hop state set and marked as Claude panes.

    Args:
        validate: If True, filter out panes where Claude Code is no longer running.
                  Set to False for operations like prune that need to see stale panes.

    Returns:
        List of PaneInfo objects for panes with hop state and Claude marker.
    """
    # Get running Claude panes for validation
    running_pane_ids = get_running_claude_pane_ids() if validate else None

    # Query all panes with hop options
    # Format: pane_id \t claude_marker \t state \t timestamp \t cwd \t session \t window
    output = run_tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\t#{@hop-claude}\t#{@hop-state}\t#{@hop-timestamp}\t#{pane_current_path}\t#{session_name}\t#{window_index}",
    )

    panes = []
    for line in output.split("\n"):
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        pane_id, claude_marker, state, timestamp_str, cwd, session, window_str = parts

        # Only include panes with Claude marker AND hop state
        if claude_marker != "1" or not state:
            continue

        # Skip stale panes if validating
        if running_pane_ids is not None and pane_id not in running_pane_ids:
            continue

        try:
            timestamp = int(timestamp_str) if timestamp_str else 0
            window = int(window_str) if window_str else 0
        except ValueError:
            timestamp = 0
            window = 0

        panes.append(
            PaneInfo(
                id=pane_id,
                state=state,
                timestamp=timestamp,
                cwd=cwd,
                session=session,
                window=window,
            )
        )

    return panes


def get_stale_panes() -> list[PaneInfo]:
    """Get panes with hop state but where Claude Code is no longer running.

    Returns:
        List of PaneInfo objects for stale panes that should be cleaned up.
    """
    running_pane_ids = get_running_claude_pane_ids()
    all_hop_panes = get_hop_panes(validate=False)
    return [p for p in all_hop_panes if p.id not in running_pane_ids]


def switch_to_pane(pane_id: str, target_session: str | None = None, target_window: int | None = None) -> bool:
    """Switch to a pane, handling cross-session navigation.

    Args:
        pane_id: The target pane ID (e.g., "%99")
        target_session: The session name (optional, will be looked up if not provided)
        target_window: The window index (optional, used for cross-session switches)

    Returns:
        True if switch was successful, False if pane not found
    """
    # Look up session if not provided
    if target_session is None:
        output = run_tmux(
            "list-panes",
            "-a",
            "-F",
            "#{pane_id}\t#{session_name}\t#{window_index}",
        )

        for line in output.split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0] == pane_id:
                target_session = parts[1]
                target_window = int(parts[2]) if parts[2] else None
                break

        if not target_session:
            run_tmux(
                "display-message",
                f"Pane {pane_id} not found",
            )
            return False

    # Get current session
    current_session = run_tmux("display-message", "-p", "#{session_name}")

    # Switch session if needed, then select the pane
    if target_session != current_session:
        # Switch to session:window to land on the correct window
        if target_window is not None:
            run_tmux("switch-client", "-t", f"{target_session}:{target_window}")
        else:
            run_tmux("switch-client", "-t", target_session)
        run_tmux("select-pane", "-t", pane_id)
    else:
        run_tmux("select-pane", "-t", pane_id)

    return True
