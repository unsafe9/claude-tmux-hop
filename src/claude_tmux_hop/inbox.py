"""Notification inbox for state change history."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .log import LOG_DIR
from .priority import priority_sort_key

INBOX_FILE = LOG_DIR / "inbox.jsonl"
MAX_ENTRIES = 50
DEFAULT_DISPLAY_LIMIT = 20

# States recorded as notifications
INBOX_STATES = {"waiting", "idle"}


@dataclass
class InboxEntry:
    """A recorded notification event."""

    timestamp: int
    state: str
    project: str
    pane_id: str
    session: str
    window: int


def record(
    state: str,
    project: str,
    pane_id: str,
    session: str,
    window: int,
) -> None:
    """Record a state change to the inbox.

    Only records states in INBOX_STATES (waiting, idle).
    """
    if state not in INBOX_STATES:
        return

    entry = {
        "ts": int(time.time()),
        "state": state,
        "project": project,
        "pane_id": pane_id,
        "session": session,
        "window": window,
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with INBOX_FILE.open("a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    _truncate_if_needed()


_MAX_INBOX_BYTES = MAX_ENTRIES * 200


def _truncate_if_needed() -> None:
    """Keep only the last MAX_ENTRIES entries."""
    try:
        if INBOX_FILE.stat().st_size <= _MAX_INBOX_BYTES:
            return
        lines = INBOX_FILE.read_text().splitlines()
        if len(lines) > MAX_ENTRIES:
            INBOX_FILE.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n")
    except OSError:
        pass


def get_entries(limit: int = DEFAULT_DISPLAY_LIMIT) -> list[InboxEntry]:
    """Read inbox entries in priority order (cycle order).

    Deduplicates by pane_id (keeps most recent per pane),
    then sorts by priority (waiting → idle), newest first within each group.

    Args:
        limit: Maximum number of entries to return

    Returns:
        List of InboxEntry in cycle order
    """
    entries: list[InboxEntry] = []
    try:
        for line in INBOX_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(InboxEntry(
                    timestamp=d["ts"],
                    state=d["state"],
                    project=d["project"],
                    pane_id=d["pane_id"],
                    session=d["session"],
                    window=d["window"],
                ))
            except (json.JSONDecodeError, KeyError):
                continue
    except OSError:
        return []

    # Sort desc first so the dedup pass keeps the most recent entry per pane,
    # then sort by priority bucket (newest-first within each) for cycle order.
    entries.sort(key=lambda e: -e.timestamp)

    seen: set[str] = set()
    unique: list[InboxEntry] = []
    for entry in entries:
        if entry.pane_id not in seen:
            seen.add(entry.pane_id)
            unique.append(entry)

    unique.sort(key=lambda e: priority_sort_key(e.state, e.timestamp))
    return unique[:limit]


def remove_pane(pane_id: str) -> None:
    """Remove all entries for a pane from the inbox."""
    try:
        # record() uses compact separators, so the key format is deterministic
        needle = f'"pane_id":"{pane_id}"'
        lines = INBOX_FILE.read_text().splitlines()
        filtered = [line for line in lines if line.strip() and needle not in line]

        if filtered:
            INBOX_FILE.write_text("\n".join(filtered) + "\n")
        else:
            INBOX_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def clear() -> None:
    """Clear all inbox entries."""
    try:
        INBOX_FILE.unlink(missing_ok=True)
    except OSError:
        pass
