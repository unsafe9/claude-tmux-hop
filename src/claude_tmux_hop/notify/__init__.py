"""Notification and focus functionality using Strategy pattern.

This module provides cross-platform notification and terminal focus capabilities
using a registry of platform-specific implementations.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time

from ..log import log_debug, log_info
from ..tmux import (
    get_global_option,
    get_pane_option,
    parse_state_set,
    set_pane_option,
    unset_pane_option,
)
from .base import (
    SUBPROCESS_TIMEOUT,
    FocusDetector,
    FocusHandler,
    Notifier,
    PaneContext,
)
from .linux import LinuxFocusDetector, LinuxFocusHandler, LinuxNotifier
from .macos import (
    MacOSFocusDetector,
    MacOSFocusHandler,
    MacOSNotifier,
    detect_terminal_app_via_tmux_client,
)
from .terminals import MACOS_BUNDLE_MAP, TERMINAL_APP_MAP
from .windows import WindowsFocusDetector, WindowsFocusHandler, WindowsNotifier

__all__ = [
    "handle_state_notifications",
    "send_notification",
    "focus_terminal",
    "should_notify",
    "should_focus_app",
    "get_platform",
    "is_terminal_focused",
    "clear_notification_stamp",
    "Notifier",
    "FocusHandler",
    "FocusDetector",
    "PaneContext",
]

# OS notification dedup: identical notifications for the same pane are
# suppressed within this window. The stamp lives in a pane option so it
# survives across hook processes and dies with the pane.
NOTIFY_COOLDOWN_SECONDS = 120
NOTIFY_STAMP_OPTION = "@hop-last-notify"
NOTIFY_FINGERPRINT_LEN = 12


# =============================================================================
# Platform Detection
# =============================================================================


def get_platform() -> str:
    """Detect the current platform.

    Returns:
        'darwin' for macOS, 'linux' for Linux, 'win32' for Windows, 'unsupported' otherwise
    """
    if sys.platform == "darwin":
        return "darwin"
    elif sys.platform.startswith("linux"):
        return "linux"
    elif sys.platform == "win32":
        return "win32"
    return "unsupported"


# =============================================================================
# Strategy Registry
# =============================================================================

# Registry of notifiers by platform
NOTIFIERS: dict[str, type[Notifier]] = {
    "darwin": MacOSNotifier,
    "linux": LinuxNotifier,
    "win32": WindowsNotifier,
}

# Registry of focus handlers by platform
FOCUS_HANDLERS: dict[str, type[FocusHandler]] = {
    "darwin": MacOSFocusHandler,
    "linux": LinuxFocusHandler,
    "win32": WindowsFocusHandler,
}

# Registry of focus detectors by platform
FOCUS_DETECTORS: dict[str, type[FocusDetector]] = {
    "darwin": MacOSFocusDetector,
    "linux": LinuxFocusDetector,
    "win32": WindowsFocusDetector,
}


# =============================================================================
# Terminal App Detection
# =============================================================================


def _get_terminal_app(session_name: str | None = None) -> str | None:
    """Get terminal app name from config or environment.

    Args:
        session_name: Optional tmux session name. Passed through to the macOS
            tmux-client detection so it can scope the client lookup to the
            session that triggered the hook.

    Returns:
        App name for focus command, or None if not detected
    """
    # Check for user override first
    configured = get_global_option("@hop-terminal-app", "")
    if configured:
        return configured

    # macOS inside tmux: env vars are inherited from the terminal that started
    # the tmux server and become stale after re-attaching from another
    # terminal app. The attached tmux client process belongs to the real
    # owner, so its ancestry beats env vars when available.
    if sys.platform == "darwin" and os.environ.get("TMUX"):
        via_client = detect_terminal_app_via_tmux_client(session_name)
        if via_client:
            return via_client

    term_program = os.environ.get("TERM_PROGRAM", "")
    term_program_app = None
    if term_program and term_program != "tmux":
        term_program_app = TERMINAL_APP_MAP.get(term_program)

    # On macOS, __CFBundleIdentifier usually works inside tmux. If TERM_PROGRAM
    # points to another known terminal, prefer it because tmux can keep a stale
    # bundle ID after the user switches terminal apps.
    bundle_id = os.environ.get("__CFBundleIdentifier", "")
    if bundle_id:
        bundle_app = None
        # Check exact match first
        if bundle_id in MACOS_BUNDLE_MAP:
            bundle_app = MACOS_BUNDLE_MAP[bundle_id]
        # Check partial match (e.g., com.jetbrains.goland.EAP)
        if bundle_app is None:
            for prefix, app_name in MACOS_BUNDLE_MAP.items():
                if bundle_id.startswith(prefix):
                    bundle_app = app_name
                    break

        if bundle_app:
            if term_program_app and term_program_app != bundle_app:
                return term_program_app
            return bundle_app

    # Windows Terminal detection via WT_SESSION env var
    if os.environ.get("WT_SESSION"):
        return "Windows Terminal"

    # Windows: check for common terminal indicators
    if sys.platform == "win32":
        if os.environ.get("ConEmuPID"):
            return "ConEmu"
        comspec = os.environ.get("ComSpec", "").lower()
        if "cmd.exe" in comspec:
            return "cmd"
        return None

    # Check TERM_PROGRAM (useful on Linux or when bundle ID not available)
    if term_program and term_program != "tmux":
        if term_program_app:
            return term_program_app

    # JetBrains detection via TERM_PROGRAM containing JediTerm
    if term_program and "JediTerm" in term_program:
        lc_terminal = os.environ.get("LC_TERMINAL", "")
        if lc_terminal:
            return lc_terminal
        return "IntelliJ IDEA"

    # Fallback: use TERM_PROGRAM as-is if set and not tmux
    if term_program and term_program != "tmux":
        return term_program

    return None


def _get_tmux_session_name() -> str | None:
    """Get the current tmux session name.

    Returns:
        Session name or None if not in tmux
    """
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


# =============================================================================
# Configuration Helpers
# =============================================================================


def should_notify(state: str) -> bool:
    """Check if notification should be sent for this state.

    Args:
        state: The state being registered

    Returns:
        True if notification should be triggered
    """
    notify_states_str = get_global_option("@hop-notify", "")
    if not notify_states_str:
        return False
    return state in parse_state_set(notify_states_str)


def should_focus_app(state: str) -> bool:
    """Check if terminal should be focused for this state.

    Args:
        state: The state being registered

    Returns:
        True if terminal focus should be triggered
    """
    focus_states_str = get_global_option("@hop-focus-app", "")
    if not focus_states_str:
        return False
    return state in parse_state_set(focus_states_str)


def _notify_fingerprint(message: str) -> str:
    return hashlib.sha1(message.encode("utf-8")).hexdigest()[:NOTIFY_FINGERPRINT_LEN]


def _is_duplicate_notification(pane_id: str, fingerprint: str) -> bool:
    """True if the same notification was sent for this pane within the cooldown."""
    raw = get_pane_option(NOTIFY_STAMP_OPTION, pane_id)
    if not raw:
        return False
    ts_str, _, stamped = raw.partition(":")
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    return stamped == fingerprint and (time.time() - ts) < NOTIFY_COOLDOWN_SECONDS


def _stamp_notification(pane_id: str, fingerprint: str) -> None:
    set_pane_option(NOTIFY_STAMP_OPTION, f"{int(time.time())}:{fingerprint}", pane_id)


def clear_notification_stamp(pane_id: str | None = None) -> None:
    """Reset the dedup stamp (called when a new user turn starts)."""
    unset_pane_option(NOTIFY_STAMP_OPTION, pane_id)


# =============================================================================
# Public API
# =============================================================================


def is_terminal_focused(
    app_name: str | None = None,
    session_name: str | None = None,
) -> bool:
    """Check if terminal app (and optionally tab) is currently focused.

    Used to suppress OS notifications when user is already looking at terminal.

    Args:
        app_name: Pre-resolved terminal app name, resolved via _get_terminal_app
            when omitted. Pass this when the caller already detected the app
            to avoid a duplicate `tmux show-option` subprocess.
        session_name: Pre-resolved tmux session name, resolved via
            _get_tmux_session_name when omitted.

    Returns:
        True if terminal is focused, False otherwise or if detection fails.
    """
    platform = get_platform()
    detector_class = FOCUS_DETECTORS.get(platform)
    if not detector_class:
        return False

    if session_name is None and os.environ.get("TMUX"):
        session_name = _get_tmux_session_name()
    if app_name is None:
        app_name = _get_terminal_app(session_name)
    if not app_name:
        return False
    return detector_class().is_focused(app_name, session_name)


def send_notification(title: str, message: str, on_click: PaneContext | None = None) -> bool:
    """Send OS notification using platform-specific strategy.

    Args:
        title: Notification title
        message: Notification body
        on_click: Optional pane context for click-to-focus action

    Returns:
        True on success
    """
    platform = get_platform()
    notifier_class = NOTIFIERS.get(platform)
    if notifier_class:
        return notifier_class().send(title, message, on_click)
    return False


def focus_terminal(
    app_name: str | None = None,
    session_name: str | None = None,
) -> bool:
    """Bring terminal app to the foreground (OS-level only).

    Tmux pane navigation is intentionally not performed here — the CLI's
    auto-hop path handles pane focus independently so both can trigger on
    the same event without either one masking the other.

    Args:
        app_name: Pre-resolved terminal app name, resolved via _get_terminal_app
            when omitted.
        session_name: Pre-resolved tmux session name, resolved via
            _get_tmux_session_name when omitted.

    Returns:
        True on success.
    """
    if session_name is None and os.environ.get("TMUX"):
        session_name = _get_tmux_session_name()
    if app_name is None:
        app_name = _get_terminal_app(session_name)
    if not app_name:
        log_debug("focus: could not detect terminal app")
        return False

    platform = get_platform()
    handler_class = FOCUS_HANDLERS.get(platform)
    if handler_class:
        return handler_class().focus(app_name, session_name)
    return False


def handle_state_notifications(
    state: str,
    project: str,
    pane_context: PaneContext | None = None,
    detail: str = "",
    allow_focus: bool = True,
) -> None:
    """Handle OS notification and terminal focus for a state change.

    Focus and notification are independent of the CLI's auto-hop path.
    The caller should invoke `do_auto_hop` separately — tmux pane hopping
    must happen whether or not the terminal app is already in front, so
    this function no longer communicates a skip signal back to the caller.

    `allow_focus` is False when the register merely re-asserted the pane's
    existing state (e.g. a repeated idle_prompt after Stop already set idle).
    App-focus has no dedup of its own, so re-firing it would keep pulling the
    user back to a pane they already left — gate it on a real transition. The
    OS notification keeps its own fingerprint+cooldown dedup, so it still runs.

    `detail` enriches the notification body (permission message, question
    text, or task summary). Identical bodies for the same pane are
    deduplicated within NOTIFY_COOLDOWN_SECONDS.
    """
    focus_enabled = should_focus_app(state)
    wants_notify = should_notify(state)
    do_focus = focus_enabled and allow_focus

    if not do_focus and not wants_notify:
        return

    # Resolve terminal identity once; the probe and the focus path both need it
    # and each resolution issues a tmux subprocess. Session name resolves
    # first so macOS tmux-client detection can scope its lookup.
    session_name = _get_tmux_session_name() if os.environ.get("TMUX") else None
    app_name = _get_terminal_app(session_name)

    # Single probe gates both paths so we don't steal focus from a user
    # already looking at the terminal.
    already_focused = is_terminal_focused(app_name, session_name)

    if do_focus:
        if already_focused:
            log_info(f"focus suppressed: terminal already focused ({state})")
        elif focus_terminal(app_name, session_name):
            log_info(f"terminal focused: {state}")
        else:
            log_debug(f"terminal focus failed (silent): {state}")

    if wants_notify:
        if already_focused:
            log_info("notification suppressed: terminal already focused")
        else:
            title = "Claude Code"
            message = f"{project} ({state}): {detail}" if detail else f"{project}: {state}"
            fingerprint = _notify_fingerprint(message)
            pane_id = pane_context.pane_id if pane_context else None
            if pane_id and _is_duplicate_notification(pane_id, fingerprint):
                log_info("notification suppressed: duplicate within cooldown")
                return
            click_context = None if focus_enabled else pane_context
            if send_notification(title, message, click_context):
                if pane_id:
                    _stamp_notification(pane_id, fingerprint)
                log_info(f"notification sent: {state}")
            else:
                log_debug(f"notification failed (silent): {state}")
