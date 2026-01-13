"""Base protocols and helpers for notification and focus strategies."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

# Subprocess timeouts (seconds)
SUBPROCESS_TIMEOUT = 5
SUBPROCESS_TIMEOUT_LONG = 10  # For slower operations like Windows PowerShell


@dataclass
class PaneContext:
    """Context for the pane requesting notification/focus."""

    pane_id: str  # e.g., "%99"
    session: str  # tmux session name
    window: int  # tmux window index
    project: str  # project name for notification


class Notifier(Protocol):
    """Protocol for sending OS notifications.

    Implementations should handle platform-specific notification mechanisms
    and fail silently on errors.
    """

    def send(
        self, title: str, message: str, on_click: PaneContext | None = None
    ) -> bool:
        """Send a notification to the user.

        Args:
            title: The notification title
            message: The notification body text
            on_click: Optional pane context for click-to-focus behavior

        Returns:
            True if notification was sent successfully, False otherwise
        """
        ...


class FocusHandler(Protocol):
    """Protocol for focusing terminal applications.

    Implementations should handle platform-specific window activation
    and optionally support tab-level focusing for specific apps.
    """

    def focus(
        self,
        app_name: str,
        session_name: str | None = None,
        pane_context: PaneContext | None = None,
    ) -> bool:
        """Bring the terminal application to the foreground.

        Args:
            app_name: Name of the application to focus
            session_name: Optional tmux session name for tab-specific focusing
            pane_context: Optional pane context for full tmux navigation

        Returns:
            True if focus was successful, False otherwise
        """
        ...


class FocusDetector(Protocol):
    """Protocol for detecting if terminal is currently focused."""

    def is_focused(self, app_name: str, session_name: str | None = None) -> bool:
        """Check if the terminal app (and optionally specific tab) is focused.

        Args:
            app_name: Name of the terminal application
            session_name: Optional tmux session name for tab-specific detection

        Returns:
            True if terminal is focused, False otherwise
        """
        ...


def run_command(
    args: list[str],
    timeout: int = SUBPROCESS_TIMEOUT,
) -> bool:
    """Run a subprocess command and return success status.

    This is a helper for strategy implementations that need to run
    external commands (osascript, notify-send, wmctrl, etc.).

    Args:
        args: Command and arguments to execute
        timeout: Timeout in seconds (default: SUBPROCESS_TIMEOUT)

    Returns:
        True if command completed successfully (returncode 0), False otherwise
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def run_command_output(
    args: list[str],
    timeout: int = SUBPROCESS_TIMEOUT,
) -> str | None:
    """Run a subprocess command and return stdout or None on failure.

    Args:
        args: Command and arguments to execute
        timeout: Timeout in seconds (default: SUBPROCESS_TIMEOUT)

    Returns:
        Stripped stdout if command succeeded, None otherwise
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.SubprocessError, OSError):
        return None


def switch_tmux_pane(ctx: PaneContext) -> None:
    """Switch to the specified tmux window and pane.

    This is a shared utility used by all platform FocusHandler implementations
    after focusing the terminal app/tab.

    Args:
        ctx: Pane context with session, window, and pane_id
    """
    subprocess.run(
        ["tmux", "select-window", "-t", f"{ctx.session}:{ctx.window}"],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["tmux", "select-pane", "-t", ctx.pane_id],
        capture_output=True,
        check=False,
    )
