"""Priority sorting logic for hop panes."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tmux import PaneInfo


# State priorities (lower = higher priority)
STATE_PRIORITY = {
    "waiting": 0,
    "idle": 1,
    "active": 2,
}

# Valid states - single source of truth for CLI validation
VALID_STATES = list(STATE_PRIORITY.keys())

# Valid cycle modes
VALID_CYCLE_MODES = ["priority", "flat"]


def group_by_state(panes: list[PaneInfo]) -> dict[str, list[PaneInfo]]:
    """Group panes by state.

    Args:
        panes: List of PaneInfo objects

    Returns:
        Dict mapping state to list of panes with that state
    """
    groups: dict[str, list[PaneInfo]] = {
        "waiting": [],
        "idle": [],
        "active": [],
    }

    for pane in panes:
        if pane.state in groups:
            groups[pane.state].append(pane)
        else:
            # Unknown state - treat as "active" (consistent with sort_all_panes)
            print(
                f"Warning: unknown state '{pane.state}' for pane {pane.id}, treating as 'active'",
                file=sys.stderr,
            )
            groups["active"].append(pane)

    return groups


def sort_within_group(panes: list[PaneInfo], state: str) -> list[PaneInfo]:
    """Sort panes within a state group.

    - waiting: oldest first (ascending timestamp)
    - idle/active: newest first (descending timestamp)

    Args:
        panes: List of panes in the same state
        state: The state of the panes

    Returns:
        Sorted list of panes
    """
    if state == "waiting":
        # Oldest first - ascending timestamp
        return sorted(panes, key=lambda p: p.timestamp)
    else:
        # Newest first - descending timestamp
        return sorted(panes, key=lambda p: -p.timestamp)


def get_cycle_group(panes: list[PaneInfo], mode: str = "priority") -> list[PaneInfo]:
    """Get panes to cycle through based on mode.

    Modes:
    - priority: Cycle within highest-priority non-empty group only
      (waiting -> idle -> active)
    - flat: All panes combined, sorted by priority then timestamp
      (waiting oldest first, then idle newest first, then active newest first)

    Args:
        panes: All panes with hop state
        mode: Cycle mode - "priority" (default) or "flat"

    Returns:
        Sorted list of panes to cycle through
    """
    groups = group_by_state(panes)

    if mode == "flat":
        # All panes combined: waiting (oldest first), idle (newest first), active (newest first)
        result: list[PaneInfo] = []
        for state in ["waiting", "idle", "active"]:
            if groups[state]:
                result.extend(sort_within_group(groups[state], state))
        return result
    else:
        # priority mode (default): cycle within single highest-priority group
        if groups["waiting"]:
            return sort_within_group(groups["waiting"], "waiting")
        elif groups["idle"]:
            return sort_within_group(groups["idle"], "idle")
        elif groups["active"]:
            return sort_within_group(groups["active"], "active")
        else:
            return []


def sort_all_panes(panes: list[PaneInfo]) -> list[PaneInfo]:
    """Sort all panes by priority for picker display.

    Args:
        panes: All panes with hop state

    Returns:
        Sorted list: waiting (oldest first), idle (newest first), active (newest first)
    """

    def sort_key(pane: PaneInfo) -> tuple[int, int]:
        priority = STATE_PRIORITY.get(pane.state, 2)
        # waiting: oldest first (ascending), others: newest first (descending)
        ts = pane.timestamp if pane.state == "waiting" else -pane.timestamp
        return (priority, ts)

    return sorted(panes, key=sort_key)
