"""Tmux operations for pane state management."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .log import log_debug, log_error, log_info

# Dialog detection constants
PROMPT_CHAR = "❯"  # Claude Code input prompt / Ink selection cursor (U+276F)
STATUS_SEPARATOR = "─"  # Box drawing character in status bar separator (U+2500)
WAITING_STALE_THRESHOLD = 30  # Seconds a pane must stay "waiting" before we
# capture-pane and verify the dialog is still active. Tuned to keep the
# status-bar polling path cheap; UserPromptSubmit/Stop hooks already flip state
# naturally when Claude sees the response, so this only catches rare
# out-of-band dialog dismissals (ctrl+C etc.).

GIT_TIMEOUT = 5  # Per-call git subprocess timeout
# Fixed-sleep fragility (known limitation): if the real claude boot takes
# longer than CLAUDE_BOOT_SLEEP, a literal-mode prompt can race ahead and
# hit the shell instead of the TUI. Replace with capture-pane polling later.
CLAUDE_BOOT_SLEEP = 3.0  # Wait for `claude` to be ready for keystrokes
SEND_KEYS_SETTLE = 0.3  # Pause between literal-prompt send and Enter

CONDUCTOR_TRUTHY = {"on", "1", "true", "yes"}



@dataclass
class PaneInfo:
    """Information about a tmux pane with hop state."""

    id: str  # e.g., "%99"
    state: str  # "waiting", "idle", "active"
    timestamp: int  # Unix timestamp
    cwd: str  # Current working directory
    session: str  # Session name
    window: int  # Window index
    task: str = ""  # One-line task summary (from Claude Code ai-title)
    wait_reason: str = ""  # Why the pane is waiting (question/plan/permission/elicitation)

    @property
    def project(self) -> str:
        """Get the project name from the working directory."""
        return os.path.basename(self.cwd) if self.cwd else "unknown"


def parse_state_set(value: str) -> set[str]:
    """Parse a comma-separated string of states into a set.

    Args:
        value: Comma-separated states (e.g., "waiting,idle")

    Returns:
        Set of lowercase state strings
    """
    return {s.strip().lower() for s in value.split(",") if s.strip()}



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


def get_current_session_window(pane_id: str | None = None) -> tuple[str, int | None]:
    """Get a tmux session name and window index.

    Args:
        pane_id: Optional pane ID to query. When omitted, tmux uses the current
            command context.

    Returns:
        Tuple of (session_name, window_index). Window may be None if parsing fails.
    """
    target = ["-t", pane_id] if pane_id else []
    current_info = run_tmux(
        "display-message",
        *target,
        "-p",
        "#{session_name}\t#{window_index}",
    )
    parts = current_info.split("\t", maxsplit=1)
    session = parts[0] if parts else ""
    try:
        window = int(parts[1]) if len(parts) > 1 and parts[1] else None
    except ValueError:
        window = None
    return session, window


def _get_pane_id(pane_id: str | None) -> str | None:
    """Get pane ID, defaulting to TMUX_PANE env var.

    Args:
        pane_id: Explicit pane ID, or None to use TMUX_PANE env var

    Returns:
        The pane ID to use, or None if not available
    """
    if pane_id is not None:
        return pane_id
    return os.environ.get("TMUX_PANE")


def _pane_target_args(pane_id: str | None) -> list[str]:
    """Return target args for pane option commands.

    Uses TMUX_PANE env var if pane_id is None.
    """
    resolved = _get_pane_id(pane_id)
    return ["-t", resolved] if resolved else []


def set_pane_state(state: str, pane_id: str | None = None, reason: str = "") -> None:
    """Set the hop state for a pane.

    Args:
        state: The state to set ("waiting", "idle", "active")
        pane_id: The pane ID, or None for current pane
        reason: Why the pane is waiting (stored only for "waiting" state)
    """
    target = _pane_target_args(pane_id)
    timestamp = str(int(time.time()))
    # Single tmux invocation: ";" separates chained commands, and unsetting a
    # never-set option mid-chain does not abort the chain. register is the
    # hot path (every hook event), so the 3 writes share one subprocess.
    args = ["set-option", "-p", *target, "@hop-state", state,
            ";", "set-option", "-p", *target, "@hop-timestamp", timestamp]
    if state == "waiting" and reason:
        args += [";", "set-option", "-p", *target, "@hop-wait-reason", reason]
    else:
        args += [";", "set-option", "-p", *target, "-u", "@hop-wait-reason"]
    run_tmux(*args)


def set_pane_task(task: str, pane_id: str | None = None) -> None:
    """Set the task summary for a pane.

    Args:
        task: One-line task summary. Empty string unsets the option.
        pane_id: The pane ID, or None for current pane
    """
    target = _pane_target_args(pane_id)
    if task:
        run_tmux("set-option", "-p", *target, "@hop-task", task)
    else:
        run_tmux("set-option", "-p", *target, "-u", "@hop-task", check=False)


def has_hop_state(pane_id: str | None = None) -> bool:
    """Check if a pane has hop state set.

    Args:
        pane_id: The pane ID, or None for current pane

    Returns:
        True if the pane has hop state set
    """
    target = _pane_target_args(pane_id)
    result = run_tmux("show-option", "-p", *target, "-qv", "@hop-state", check=False)
    return bool(result)


def clear_pane_state(pane_id: str | None = None) -> None:
    """Clear the hop state from a pane.

    Args:
        pane_id: The pane ID, or None for current pane
    """
    target = _pane_target_args(pane_id)
    options = ("@hop-state", "@hop-timestamp", "@hop-task",
               "@hop-wait-reason", "@hop-last-notify")
    args: list[str] = []
    for opt in options:
        if args:
            args.append(";")
        args += ["set-option", "-p", *target, "-u", opt]
    run_tmux(*args, check=False)


def get_global_option(name: str, default: str = "") -> str:
    """Get a tmux global option value.

    Args:
        name: Option name (e.g., "@hop-auto")
        default: Default value if option not set

    Returns:
        The option value, or default if not set
    """
    result = run_tmux("show-option", "-gqv", name, check=False)
    return result if result else default


def set_global_option(name: str, value: str) -> None:
    """Set a tmux global option.

    Args:
        name: Option name (e.g., "@hop-previous-pane")
        value: Value to set
    """
    run_tmux("set-option", "-g", name, value)


def _run_git(*args: str, cwd: str) -> str:
    """Run a git command in `cwd` and return stdout; "" on any failure."""
    if not cwd:
        return ""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_git_context(cwd: str) -> dict[str, str]:
    """Return git branch + worktree root for `cwd`, or {} if not in a repo."""
    branch = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    worktree_root = _run_git("rev-parse", "--show-toplevel", cwd=cwd)
    if not branch and not worktree_root:
        return {}
    out: dict[str, str] = {}
    if branch:
        out["branch"] = branch
    if worktree_root:
        out["worktree_root"] = worktree_root
    return out


def has_session(name: str) -> bool:
    """Return True if a tmux session with the given name exists."""
    if not name:
        return False
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.returncode == 0


def spawn_session(name: str, cwd: str) -> None:
    """Create a detached tmux session running `claude`.

    Caller is responsible for sleeping `CLAUDE_BOOT_SLEEP` before sending
    further keystrokes.
    """
    run_tmux("new-session", "-d", "-s", name, "-c", cwd)
    # `-t name:` (trailing colon) forces tmux to treat the target as a session,
    # not a window name — without it, a stray window named `name` elsewhere
    # would win the resolution and the send-keys lands on the wrong pane.
    run_tmux("send-keys", "-t", f"{name}:", "claude", "Enter")


def spawn_window(
    session: str,
    cwd: str,
    prompt: str,
    window_name: str | None = None,
    switch: bool = True,
) -> str:
    """Open a new window running `claude` with `prompt` pre-submitted.

    Creates the session if missing (uses its first window). Returns the
    window id (e.g., "@42"). Switches the calling client to the new window
    when `switch=True`.
    """
    log_info(
        f"spawn-task: session={session} cwd={cwd} "
        f"window_name={window_name or ''} switch={switch}"
    )

    # `session:` (trailing colon) forces tmux to treat the target as a
    # session — without it, a window named `session` somewhere else wins
    # the resolution and tmux errors with "index N in use" (or worse,
    # creates the window in the wrong session).
    session_target = f"{session}:"
    if not has_session(session):
        spawn_session(session, cwd)
        time.sleep(CLAUDE_BOOT_SLEEP)
        window_id = run_tmux(
            "display-message", "-p", "-t", session_target, "#{window_id}"
        )
    else:
        new_window_args = ["new-window", "-P", "-F", "#{window_id}",
                           "-t", session_target, "-c", cwd]
        if window_name:
            new_window_args.extend(["-n", window_name])
        window_id = run_tmux(*new_window_args)
        run_tmux("send-keys", "-t", window_id, "claude", "Enter")
        time.sleep(CLAUDE_BOOT_SLEEP)

    # send-keys -l (literal) prevents tmux from interpreting special chars
    # inside the prompt; Enter is delivered separately as a real key.
    if prompt:
        run_tmux("send-keys", "-t", window_id, "-l", prompt)
        time.sleep(SEND_KEYS_SETTLE)
        run_tmux("send-keys", "-t", window_id, "Enter")

    if switch:
        try:
            run_tmux("switch-client", "-t", window_id)
        except RuntimeError as e:
            log_debug(f"spawn-task: switch-client failed: {e}")

    return window_id


def _get_conductor_session() -> str:
    """Conductor session name (single source for filtering)."""
    return get_global_option("@hop-conductor-session", "conductor")


def _is_conductor_enabled() -> bool:
    """Whether the conductor feature is enabled via `@hop-conductor-enabled`."""
    return get_global_option("@hop-conductor-enabled", "off").strip().lower() in CONDUCTOR_TRUTHY


def resolve_conductor_dir() -> Path:
    """Resolve the workbench directory (honoring `@hop-conductor-dir`)."""
    from .paths import get_default_conductor_dir

    custom = get_global_option("@hop-conductor-dir", "")
    if custom:
        expanded = os.path.expandvars(os.path.expanduser(custom))
        return Path(expanded).resolve()
    return get_default_conductor_dir()


def spawn_conductor_session(name: str, workbench: Path, own_bin: Path) -> None:
    """Create a detached tmux session hosting the conductor `claude`.

    The session's only window runs `exec claude` directly — when claude exits
    the window closes, the session ends, and the next attach attempt will
    recreate fresh. Tmux's `-e` flag injects session-scoped env vars that
    propagate to every shell/pane in the session, so the hook fast-path
    (`CLAUDE_TMUX_HOP_CONDUCTOR=1`) and the plugin bin (`PATH`) work without
    a wrapping shell. `PATH` is captured at session-creation time and frozen
    for the session's lifetime.
    """
    current_path = os.environ.get("PATH", "")
    augmented_path = f"{own_bin}:{current_path}" if current_path else str(own_bin)
    run_tmux(
        "new-session", "-d",
        "-e", "CLAUDE_TMUX_HOP_CONDUCTOR=1",
        "-e", f"PATH={augmented_path}",
        "-s", name,
        "-c", str(workbench),
        "exec claude",
    )


def kill_session_if_exists(name: str) -> bool:
    """Kill the named tmux session if it exists. Returns True if killed."""
    if not has_session(name):
        return False
    run_tmux("kill-session", "-t", name, check=False)
    return True


def send_prompt_to_pane(pane_id: str, prompt: str, switch: bool = True) -> None:
    """Inject `prompt` (and Enter) into an existing pane."""
    log_info(f"send-prompt: pane={pane_id} switch={switch}")
    run_tmux("send-keys", "-t", pane_id, "-l", prompt)
    time.sleep(SEND_KEYS_SETTLE)
    run_tmux("send-keys", "-t", pane_id, "Enter")
    if switch:
        try:
            switch_to_pane(pane_id)
        except RuntimeError as e:
            log_debug(f"send-prompt: switch_to_pane failed: {e}")


def _is_interactive_claude_on_tty(tty: str) -> bool:
    """Check if an interactive Claude Code session is running on a tty.

    Uses ps to get all processes on the tty and checks for 'claude' command
    without -p/--print flags (which indicate non-interactive mode).

    Args:
        tty: The tty device path (e.g., "/dev/ttys042")

    Returns:
        True if an interactive Claude Code session is running.
    """
    try:
        result = subprocess.run(
            ["ps", "-t", tty, "-o", "args="],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.splitlines():
            # Check if this is a claude command
            # Match: "claude", "/path/to/claude", "claude arg1 arg2"
            parts = line.split()
            if not parts:
                continue

            cmd = parts[0]
            # Get the base command name (handle paths like /usr/local/bin/claude)
            cmd_name = os.path.basename(cmd)

            if cmd_name.lower() == "claude":
                # Check for non-interactive flags
                args = parts[1:] if len(parts) > 1 else []
                if "-p" in args or "--print" in args:
                    continue  # Skip non-interactive mode
                return True

        return False
    except (subprocess.SubprocessError, OSError):
        return False


def get_claude_panes_by_process() -> list[dict]:
    """Find all panes running interactive Claude Code by checking processes.

    Uses ps to check processes on each pane's tty for the 'claude' command.
    Excludes panes running Claude with -p/--print (non-interactive mode).
    Filters out the conductor session — it must never be cycled into.

    Returns:
        List of dicts with pane info for each Claude pane found.
    """
    output = run_tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\t#{pane_tty}\t#{pane_current_path}\t#{session_name}\t#{window_index}",
    )

    conductor_session = _get_conductor_session()
    panes = []
    for line in output.split("\n"):
        if not line:
            continue

        parts = line.split("\t", maxsplit=4)
        if len(parts) < 5:
            continue

        pane_id, tty, cwd, session, window_str = parts

        if session == conductor_session:
            continue

        if tty and _is_interactive_claude_on_tty(tty):
            panes.append({
                "id": pane_id,
                "cwd": cwd,
                "session": session,
                "window": int(window_str) if window_str else 0,
            })

    return panes


def get_running_claude_pane_ids() -> set[str]:
    """Get the set of pane IDs currently running interactive Claude Code.

    Returns:
        Set of pane IDs (e.g., {"%0", "%5"}) running Claude Code.
    """
    return {p["id"] for p in get_claude_panes_by_process()}


def get_hop_panes(validate: bool = True) -> list[PaneInfo]:
    """Get all panes with hop state set.

    Args:
        validate: If True, filter out panes where Claude Code is no longer running.
                  Set to False for operations like prune that need to see stale panes.

    Returns:
        List of PaneInfo objects for panes with hop state.
    """
    # Get running Claude panes for validation
    running_pane_ids = get_running_claude_pane_ids() if validate else None
    conductor_session = _get_conductor_session()

    # Query all panes with hop options
    # Format: pane_id \t state \t timestamp \t cwd \t session \t window \t task \t wait_reason
    output = run_tmux(
        "list-panes",
        "-a",
        "-F",
        "#{pane_id}\t#{@hop-state}\t#{@hop-timestamp}\t#{pane_current_path}\t#{session_name}\t#{window_index}\t#{@hop-task}\t#{@hop-wait-reason}",
    )

    panes = []
    for line in output.split("\n"):
        if not line:
            continue

        parts = line.split("\t", maxsplit=7)
        if len(parts) < 6:
            continue

        pane_id, state, timestamp_str, cwd, session, window_str = parts[:6]
        task = parts[6] if len(parts) >= 7 else ""
        wait_reason = parts[7] if len(parts) >= 8 else ""

        # Only include panes with hop state
        if not state:
            continue

        # Conductor session is never part of the hop cycle.
        if session == conductor_session:
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
                task=task,
                wait_reason=wait_reason,
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


def switch_to_pane(
    pane_id: str,
    target_session: str | None = None,
    target_window: int | None = None,
    store_previous: bool = True,
) -> bool:
    """Switch to a pane, handling cross-session and cross-window navigation.

    Args:
        pane_id: The target pane ID (e.g., "%99")
        target_session: The session name (optional, will be looked up if not provided)
        target_window: The window index (optional, used for cross-session/window switches)
        store_previous: If True, store current pane in @hop-previous-pane for jump-back

    Returns:
        True if switch was successful, False if pane not found or tmux rejected the switch
    """
    # Capture origin up front; persist only after a successful switch so a
    # failed call can't poison @hop-previous-pane.
    origin_pane = get_current_pane() if store_previous else None

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
            parts = line.split("\t", maxsplit=2)
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

    # Get current session and window
    current_session, current_window = get_current_session_window()

    # Any of switch-client/select-window/select-pane may fail if the target
    # has disappeared between lookup and now; report that as a soft failure
    # so callers (cmd_cycle, etc.) can prune stale entries instead of crashing.
    try:
        if target_session != current_session:
            if target_window is not None:
                run_tmux("switch-client", "-t", f"{target_session}:{target_window}")
            else:
                run_tmux("switch-client", "-t", target_session)
        elif target_window is not None and target_window != current_window:
            run_tmux("select-window", "-t", f"{target_session}:{target_window}")

        run_tmux("select-pane", "-t", pane_id)
    except RuntimeError:
        return False

    if origin_pane and origin_pane != pane_id:
        set_global_option("@hop-previous-pane", origin_pane)

    return True


def capture_pane_content(pane_id: str, last_lines: int = 15) -> str:
    """Capture the last N lines of a tmux pane's visible content.

    Args:
        pane_id: The pane ID to capture
        last_lines: Number of lines from the bottom to capture

    Returns:
        The captured content, or empty string on failure
    """
    return run_tmux(
        "capture-pane", "-t", pane_id, "-p", "-S", f"-{last_lines}",
        check=False,
    )


def _is_separator_line(stripped: str) -> bool:
    """Check if a line is a Claude Code status bar separator (all ─ chars)."""
    return bool(stripped) and all(c == STATUS_SEPARATOR for c in stripped)


def has_active_dialog(content: str) -> bool:
    """Check if a Claude Code interactive dialog is active in pane content.

    Since the input prompt (❯) and the Ink selection cursor (❯) are the same
    character, detection is positional rather than character-based.

    Claude Code layout when prompt is visible (dialog dismissed):
        [content] → ─── → ❯ [input] → ─── → [status metadata]
    Layout when a dialog is active:
        [content] → [? question / ❯ option / options] → ─── → [status metadata]

    Scans from the bottom, skips the status bar, and checks whether the first
    content line above the separator is the input prompt (❯).

    Args:
        content: Captured pane content (last N lines)

    Returns:
        True if a dialog appears to be active, False if dismissed
    """
    if not content or not content.strip():
        return True  # Conservative: empty/whitespace = assume active

    lines = content.splitlines()

    # Scan from bottom: skip status metadata, find first separator,
    # then check whether the line above it is the input prompt.
    found_separator = False
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue

        if _is_separator_line(stripped):
            found_separator = True
            continue

        if found_separator:
            # First content line above a separator
            if stripped == PROMPT_CHAR or stripped.startswith(f"{PROMPT_CHAR} "):
                return False  # Input prompt visible → dialog dismissed
            break  # Not the prompt → dialog or other content

        # Below first separator → status bar metadata → skip

    # Prompt not found above separator → assume dialog active (conservative)
    return True


def validate_waiting_panes(panes: list[PaneInfo]) -> None:
    """Check stale "waiting" panes and flip to "idle" if dialog is dismissed.

    Mutates panes in-place when a stale waiting pane's dialog is no longer active.
    Also records the flip to the notification inbox so both views stay in sync.

    Args:
        panes: List of PaneInfo objects (modified in-place)
    """
    from . import inbox

    now = int(time.time())

    for pane in panes:
        if pane.state != "waiting":
            continue

        if (now - pane.timestamp) < WAITING_STALE_THRESHOLD:
            continue

        content = capture_pane_content(pane.id)
        if not content:
            continue  # Pane gone or empty, skip

        if has_active_dialog(content):
            continue

        try:
            set_pane_state("idle", pane.id)
        except RuntimeError:
            continue  # Pane may have disappeared

        pane.state = "idle"
        pane.timestamp = now
        pane.wait_reason = ""
        inbox.record(
            state="idle",
            project=pane.project,
            pane_id=pane.id,
            session=pane.session,
            window=pane.window,
            task=pane.task,
        )
        log_info(f"validate: {pane.id} flipped waiting → idle (dialog dismissed)")
