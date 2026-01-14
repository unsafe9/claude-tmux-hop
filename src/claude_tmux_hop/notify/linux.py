"""Linux notification and focus handlers."""

from __future__ import annotations

import os
import shutil

from .base import run_command, run_command_output, PaneContext, switch_tmux_pane


class LinuxNotifier:
    """Send notifications on Linux using notify-send."""

    def send(self, title: str, message: str, on_click: PaneContext | None = None) -> bool:
        """Send a desktop notification using notify-send.

        Args:
            title: Notification title
            message: Notification body
            on_click: Optional pane context for click-to-focus (limited support on Linux)

        Returns:
            True if notification was sent successfully
        """
        if not shutil.which("notify-send"):
            return False

        # Note: notify-send --action requires libnotify 0.7.8+ and desktop support
        # For now, we don't implement click-to-focus on Linux as it requires
        # D-Bus handling which is complex. Just send a regular notification.
        return run_command(["notify-send", title, message])


class LinuxFocusHandler:
    """Focus terminal windows on Linux using wmctrl or xdotool."""

    def focus(self, app_name: str, session_name: str | None = None,
              pane_context: PaneContext | None = None) -> bool:
        """Bring terminal window to foreground.

        Tries wmctrl first, falls back to xdotool.

        Args:
            app_name: Name of the application window to focus
            session_name: Optional tmux session name for window matching
            pane_context: Optional tmux pane context for pane-level focusing

        Returns:
            True if window was focused successfully
        """
        # First: focus the window
        focused = self._focus_window(app_name, session_name)

        # Then: switch tmux pane if context provided
        if focused and pane_context:
            switch_tmux_pane(pane_context)

        return focused

    def _focus_window(self, app_name: str, session_name: str | None) -> bool:
        """Focus the terminal window using wmctrl or xdotool."""
        # Try wmctrl first (more reliable)
        if shutil.which("wmctrl"):
            # If session_name provided, try to match window title
            search_term = session_name if session_name else app_name
            if run_command(["wmctrl", "-a", search_term]):
                return True

        # Fallback to xdotool
        if shutil.which("xdotool"):
            search_term = session_name if session_name else app_name
            if run_command(["xdotool", "search", "--name", search_term, "windowactivate"]):
                return True

        return False


class LinuxFocusDetector:
    """Detect if Linux terminal window is currently focused."""

    def is_focused(self, app_name: str, session_name: str | None = None) -> bool:
        """Check if the terminal window is focused.

        Uses xdotool to get the active window name on X11.
        Wayland detection is limited - returns False (show notification anyway).

        Args:
            app_name: Name of the application to check
            session_name: Optional tmux session name for more specific matching

        Returns:
            True if the terminal window is focused, False otherwise
        """
        # Check for Wayland - can't reliably detect focused window
        if os.environ.get("WAYLAND_DISPLAY"):
            # On Wayland, we can't easily detect focused window
            # Return False to always show notification (safe default)
            return False

        # X11: use xdotool
        if not shutil.which("xdotool"):
            return False  # Can't detect, assume not focused

        active_window = run_command_output(
            ["xdotool", "getactivewindow", "getwindowname"]
        )

        if not active_window:
            return False

        # Check if window name contains app name or session name
        search_term = session_name if session_name else app_name
        return search_term.lower() in active_window.lower()
