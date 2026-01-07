"""Tmux operations for pane state management."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from .log import log_debug, log_error


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


def set_pane_state(state: str, pane_id: str | None = None) -> None:
    """Set the hop state for a pane.

    Args:
        state: The state to set ("waiting", "idle", "active")
        pane_id: The pane ID, or None for current pane
    """
    timestamp = str(int(time.time()))

    if pane_id:
        run_tmux("set-option", "-p", "-t", pane_id, "@hop-state", state)
        run_tmux("set-option", "-p", "-t", pane_id, "@hop-timestamp", timestamp)
    else:
        run_tmux("set-option", "-p", "@hop-state", state)
        run_tmux("set-option", "-p", "@hop-timestamp", timestamp)


def init_pane(pane_id: str | None = None) -> None:
    """Initialize a pane as a Claude Code pane.

    Sets a marker to indicate this pane has an active Claude Code session.

    Args:
        pane_id: The pane ID, or None for current pane
    """
    if pane_id:
        run_tmux("set-option", "-p", "-t", pane_id, "@hop-claude", "1")
    else:
        run_tmux("set-option", "-p", "@hop-claude", "1")


def is_claude_pane(pane_id: str | None = None) -> bool:
    """Check if a pane is marked as a Claude Code pane.

    Args:
        pane_id: The pane ID, or None for current pane

    Returns:
        True if the pane has the Claude marker set
    """
    try:
        if pane_id:
            result = run_tmux(
                "show-option", "-p", "-t", pane_id, "-qv", "@hop-claude", check=False
            )
        else:
            result = run_tmux("show-option", "-p", "-qv", "@hop-claude", check=False)
        return result == "1"
    except subprocess.SubprocessError:
        return False


def clear_pane_state(pane_id: str | None = None) -> None:
    """Clear the hop state and Claude marker from a pane.

    Args:
        pane_id: The pane ID, or None for current pane
    """
    if pane_id:
        run_tmux("set-option", "-p", "-t", pane_id, "-u", "@hop-claude", check=False)
        run_tmux("set-option", "-p", "-t", pane_id, "-u", "@hop-state", check=False)
        run_tmux("set-option", "-p", "-t", pane_id, "-u", "@hop-timestamp", check=False)
    else:
        run_tmux("set-option", "-p", "-u", "@hop-claude", check=False)
        run_tmux("set-option", "-p", "-u", "@hop-state", check=False)
        run_tmux("set-option", "-p", "-u", "@hop-timestamp", check=False)


def get_claude_panes_by_process() -> list[dict]:
    """Find all panes running Claude Code by checking process name.

    Claude Code shows as a semver version (e.g., "2.0.76") in pane_current_command.

    Returns:
        List of dicts with pane info for each Claude pane found.
    """
    import re

    output = run_tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{session_name}\t#{window_index}",
    )

    panes = []
    semver_pattern = re.compile(r"^\d+\.\d+\.\d+$")

    for line in output.split("\n"):
        if not line:
            continue

        parts = line.split("\t")
        if len(parts) < 5:
            continue

        pane_id, command, cwd, session, window_str = parts

        if semver_pattern.match(command):
            panes.append({
                "id": pane_id,
                "command": command,
                "cwd": cwd,
                "session": session,
                "window": int(window_str) if window_str else 0,
            })

    return panes


def get_hop_panes() -> list[PaneInfo]:
    """Get all panes with hop state set and marked as Claude panes.

    Returns:
        List of PaneInfo objects for panes with hop state and Claude marker.
    """
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


def switch_to_pane(pane_id: str, target_session: str | None = None) -> bool:
    """Switch to a pane, handling cross-session navigation.

    Args:
        pane_id: The target pane ID (e.g., "%99")
        target_session: The session name (optional, will be looked up if not provided)

    Returns:
        True if switch was successful, False if pane not found
    """
    # Look up session if not provided
    if target_session is None:
        output = run_tmux(
            "list-panes",
            "-a",
            "-F",
            "#{pane_id}\t#{session_name}",
        )

        for line in output.split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] == pane_id:
                target_session = parts[1]
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
        run_tmux("switch-client", "-t", target_session)
        run_tmux("select-pane", "-t", pane_id)
    else:
        run_tmux("select-pane", "-t", pane_id)

    return True
