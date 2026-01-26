"""Notification and focus functionality using Strategy pattern.

This module provides cross-platform notification and terminal focus capabilities
using a registry of platform-specific implementations.
"""

from __future__ import annotations

import os
import subprocess
import sys

from ..log import log_debug, log_info
from ..tmux import get_global_option, parse_state_set
from .base import (
    SUBPROCESS_TIMEOUT,
    FocusDetector,
    FocusHandler,
    Notifier,
    PaneContext,
)
from .linux import LinuxFocusDetector, LinuxFocusHandler, LinuxNotifier
from .macos import MacOSFocusDetector, MacOSFocusHandler, MacOSNotifier
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
    "Notifier",
    "FocusHandler",
    "FocusDetector",
    "PaneContext",
]


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


def _get_terminal_app() -> str | None:
    """Get terminal app name from config or environment.

    Returns:
        App name for focus command, or None if not detected
    """
    # Check for user override first
    configured = get_global_option("@hop-terminal-app", "")
    if configured:
        return configured

    # On macOS, prefer __CFBundleIdentifier (works inside tmux)
    bundle_id = os.environ.get("__CFBundleIdentifier", "")
    if bundle_id:
        # Check exact match first
        if bundle_id in MACOS_BUNDLE_MAP:
            return MACOS_BUNDLE_MAP[bundle_id]
        # Check partial match (e.g., com.jetbrains.goland.EAP)
        for prefix, app_name in MACOS_BUNDLE_MAP.items():
            if bundle_id.startswith(prefix):
                return app_name

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
        return "Windows Terminal"

    # Check TERM_PROGRAM (useful on Linux or when bundle ID not available)
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program and term_program != "tmux":
        if term_program in TERMINAL_APP_MAP:
            return TERMINAL_APP_MAP[term_program]

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


# =============================================================================
# Public API
# =============================================================================


def is_terminal_focused() -> bool:
    """Check if terminal app (and optionally tab) is currently focused.

    Used to suppress OS notifications when user is already looking at terminal.

    Returns:
        True if terminal is focused, False otherwise or if detection fails
    """
    platform = get_platform()
    detector_class = FOCUS_DETECTORS.get(platform)
    if not detector_class:
        return False  # Assume not focused if can't detect

    app_name = _get_terminal_app()
    if not app_name:
        return False

    session_name = _get_tmux_session_name()
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


def focus_terminal(pane_context: PaneContext | None = None) -> bool:
    """Bring terminal app to foreground and switch to pane.

    Args:
        pane_context: Optional context for tmux pane navigation

    Returns:
        True on success
    """
    app_name = _get_terminal_app()
    if not app_name:
        log_debug("focus: could not detect terminal app")
        return False

    platform = get_platform()
    handler_class = FOCUS_HANDLERS.get(platform)
    if handler_class:
        session_name = _get_tmux_session_name()
        return handler_class().focus(app_name, session_name, pane_context)
    return False


def handle_state_notifications(state: str, project: str, pane_context: PaneContext | None = None) -> None:
    """Handle all notification actions for a state change.

    Args:
        state: The new state ("waiting", "idle", "active")
        project: Project name for notification message
        pane_context: Optional pane context for click-to-focus and tmux navigation
    """
    wants_focus = should_focus_app(state)
    wants_notify = should_notify(state)

    # If neither enabled, nothing to do
    if not wants_focus and not wants_notify:
        return

    # Check focus state BEFORE focus_terminal() changes it
    already_focused = is_terminal_focused() if wants_notify else False

    # Terminal focus (if enabled) - full navigation
    if wants_focus:
        if focus_terminal(pane_context):
            log_info(f"terminal focused: {state}")
        else:
            log_debug(f"terminal focus failed (silent): {state}")

    # System notification (if enabled)
    if wants_notify:
        # Smart suppression: skip if terminal was already focused before we acted
        if already_focused:
            log_info(f"notification suppressed: terminal already focused")
            return

        title = "Claude Code"
        message = f"{project}: {state}"

        # If focus is disabled, enable click-to-focus in notification
        click_context = None if wants_focus else pane_context

        if send_notification(title, message, click_context):
            log_info(f"notification sent: {state}")
        else:
            log_debug(f"notification failed (silent): {state}")
