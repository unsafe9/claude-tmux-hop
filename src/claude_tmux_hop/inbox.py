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
    task: str = ""
    reason: str = ""
    branch: str = ""


def record(
    state: str,
    project: str,
    pane_id: str,
    session: str,
    window: int,
    task: str = "",
    reason: str = "",
    branch: str = "",
) -> None:
    """Record a state change to the inbox.

    Only waiting/idle transitions are appended. For non-inbox states
    (e.g. active) the pane's prior entry is dropped — the user has
    attended to it, so leaving a stale waiting/idle record would skew
    priority cycling toward panes that aren't actually pending.

    The conductor session never lands in the inbox — its claude TUI is
    the user's command surface, not a tracked task. Function-local import
    avoids a tmux↔inbox cycle.
    """
    from .tmux import _get_conductor_session
    if session == _get_conductor_session():
        return

    if state not in INBOX_STATES:
        remove_pane(pane_id)
        return

    entry = {
        "ts": int(time.time()),
        "state": state,
        "project": project,
        "pane_id": pane_id,
        "session": session,
        "window": window,
        "task": task,
        "reason": reason,
        "branch": branch,
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
                    task=d.get("task", ""),
                    reason=d.get("reason", ""),
                    branch=d.get("branch", ""),
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
    remove_panes({pane_id})


def remove_panes(pane_ids: set[str]) -> None:
    """Remove all entries for the given panes in a single file rewrite."""
    if not pane_ids:
        return
    try:
        # record() uses compact separators, so the key format is deterministic
        needles = {f'"pane_id":"{pid}"' for pid in pane_ids}
        content = INBOX_FILE.read_text()
        # Active-state hooks fire remove_pane on every transition; skipping
        # the rewrite when no pane has an entry saves a write + fsync per call.
        if not any(needle in content for needle in needles):
            return
        filtered = [
            line for line in content.splitlines()
            if line.strip() and not any(needle in line for needle in needles)
        ]

        if filtered:
            INBOX_FILE.write_text("\n".join(filtered) + "\n")
        else:
            INBOX_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def backfill_git_identity(cwd_by_pane: dict[str, str], resolve) -> bool:
    """Upgrade legacy entries (recorded before git identity existed) in place.

    Lines without a "branch" key get project/branch re-resolved from their
    pane's *current* cwd via `resolve` (= tmux.get_git_identity, injected to
    keep this module tmux-free). Upgraded lines gain the key, so each legacy
    entry costs one git call ever. Returns True when the file was rewritten.
    """
    if not cwd_by_pane:
        return False
    try:
        lines = INBOX_FILE.read_text().splitlines()
    except OSError:
        return False

    upgrades: dict[str, str] = {}
    for line in lines:
        # Compact separators make the key format deterministic; the substring
        # test is just a fast-path filter before parsing.
        if not line.strip() or '"branch"' in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        cwd = cwd_by_pane.get(d.get("pane_id", ""))
        if "branch" in d or not cwd:
            continue
        branch, repo = resolve(cwd)
        if repo:
            d["project"] = repo
        d["branch"] = branch
        upgrades[line] = json.dumps(d, separators=(",", ":"))

    if not upgrades:
        return False

    # resolve() ran git per legacy entry above, so re-read just before the
    # rewrite — a register hook may have appended entries in that window.
    try:
        lines = INBOX_FILE.read_text().splitlines()
        out = [upgrades.get(line, line) for line in lines if line.strip()]
        INBOX_FILE.write_text("\n".join(out) + "\n")
    except OSError:
        return False
    return True


def clear() -> None:
    """Clear all inbox entries."""
    try:
        INBOX_FILE.unlink(missing_ok=True)
    except OSError:
        pass
