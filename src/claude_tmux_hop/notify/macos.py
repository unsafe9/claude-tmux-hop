"""macOS implementation for notification and focus functionality."""

from __future__ import annotations

import shutil
import subprocess

from .base import SUBPROCESS_TIMEOUT, PaneContext, run_command, switch_tmux_pane


def _escape_applescript_string(s: str) -> str:
    """Escape a string for use inside AppleScript double quotes.

    Handles double quotes and backslashes which have special meaning in AppleScript.

    Args:
        s: The string to escape

    Returns:
        The escaped string safe for AppleScript double-quoted strings
    """
    # Escape backslashes first, then double quotes
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _run_osascript(script: str) -> bool:
    """Run an AppleScript and return success status.

    Args:
        script: The AppleScript to execute

    Returns:
        True if the script executed successfully, False otherwise
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _run_osascript_output(script: str) -> str | None:
    """Run AppleScript and return stdout.

    Args:
        script: The AppleScript to execute

    Returns:
        The stdout output if successful, None otherwise
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.SubprocessError, OSError):
        return None


def _has_terminal_notifier() -> bool:
    """Check if terminal-notifier is available."""
    return shutil.which("terminal-notifier") is not None


def _get_bundle_id() -> str | None:
    """Get the bundle ID of the current terminal app."""
    import os

    return os.environ.get("__CFBundleIdentifier")


def _focus_iterm_tab(session_name: str) -> bool:
    """Focus the iTerm2 tab containing the tmux session.

    Searches through all iTerm windows, tabs, and sessions to find one
    whose name contains the given session name, then focuses that tab/window.

    Args:
        session_name: The tmux session name to find

    Returns:
        True if a matching tab was found and focused, False otherwise
    """
    session_escaped = _escape_applescript_string(session_name)
    script = f'''
tell application "iTerm"
    activate
    set found to false
    repeat with aWindow in windows
        repeat with aTab in tabs of aWindow
            repeat with aSession in sessions of aTab
                if name of aSession contains "{session_escaped}" then
                    select aTab
                    select aWindow
                    set found to true
                    exit repeat
                end if
            end repeat
            if found then exit repeat
        end repeat
        if found then exit repeat
    end repeat
end tell
'''
    return _run_osascript(script)


def _focus_terminal_window(session_name: str) -> bool:
    """Focus the Terminal.app window containing the tmux session.

    Searches Terminal windows for one whose name contains the session name,
    then brings that window to the front.

    Args:
        session_name: The tmux session name to find

    Returns:
        True if a matching window was found and focused, False otherwise
    """
    session_escaped = _escape_applescript_string(session_name)
    script = f'''
tell application "Terminal"
    activate
    set targetWindow to null
    repeat with aWindow in windows
        if name of aWindow contains "{session_escaped}" then
            set targetWindow to aWindow
            exit repeat
        end if
    end repeat
    if targetWindow is not null then
        set index of targetWindow to 1
        set selected tab of targetWindow to (first tab of targetWindow whose busy is true or selected is true)
    end if
end tell
'''
    return _run_osascript(script)


class MacOSNotifier:
    """macOS notification sender using osascript or terminal-notifier.

    Uses AppleScript's 'display notification' command for basic notifications,
    or terminal-notifier for click-to-focus notifications when available.
    """

    def send(
        self, title: str, message: str, on_click: PaneContext | None = None
    ) -> bool:
        """Send a notification using osascript or terminal-notifier.

        Args:
            title: The notification title
            message: The notification body text
            on_click: Optional pane context for click-to-focus behavior

        Returns:
            True if notification was sent successfully, False otherwise
        """
        if on_click and _has_terminal_notifier():
            return self._send_terminal_notifier(title, message, on_click)
        return self._send_applescript(title, message)

    def _send_applescript(self, title: str, message: str) -> bool:
        """Send notification via AppleScript.

        Args:
            title: The notification title
            message: The notification body text

        Returns:
            True if notification was sent successfully, False otherwise
        """
        title_escaped = _escape_applescript_string(title)
        message_escaped = _escape_applescript_string(message)
        script = f'display notification "{message_escaped}" with title "{title_escaped}"'
        return _run_osascript(script)

    def _send_terminal_notifier(
        self, title: str, message: str, ctx: PaneContext
    ) -> bool:
        """Send notification via terminal-notifier with click action.

        Args:
            title: The notification title
            message: The notification body text
            ctx: Pane context for click-to-focus behavior

        Returns:
            True if notification was sent successfully, False otherwise
        """
        # Build tmux switch command for click action
        click_cmd = f"tmux switch-client -t '{ctx.session}:{ctx.window}' && tmux select-pane -t '{ctx.pane_id}'"

        args = [
            "terminal-notifier",
            "-title",
            title,
            "-message",
            message,
            "-execute",
            click_cmd,
        ]

        # Add -activate for the terminal app
        bundle_id = _get_bundle_id()
        if bundle_id:
            args.extend(["-activate", bundle_id])

        return run_command(args)


class MacOSFocusHandler:
    """macOS focus handler using AppleScript.

    Supports tab-specific focusing for iTerm2 and Terminal.app,
    with fallback to simple app activation for other apps.
    """

    def focus(
        self,
        app_name: str,
        session_name: str | None = None,
        pane_context: PaneContext | None = None,
    ) -> bool:
        """Bring the terminal application to the foreground.

        For iTerm2 and Terminal.app, attempts to find and focus the specific
        tab/window containing the tmux session. Falls back to simple app
        activation if session_name is not provided or no matching tab is found.

        Args:
            app_name: Name of the application to focus
            session_name: Optional tmux session name for tab-specific focusing
            pane_context: Optional pane context for tmux window/pane switching

        Returns:
            True if focus was successful, False otherwise
        """
        # First: focus the app/tab
        focused = self._focus_app_and_tab(app_name, session_name)

        # Then: switch tmux window/pane if context provided
        if focused and pane_context:
            switch_tmux_pane(pane_context)

        return focused

    def _focus_app_and_tab(self, app_name: str, session_name: str | None) -> bool:
        """Focus the app and optionally a specific tab.

        Args:
            app_name: Name of the application to focus
            session_name: Optional tmux session name for tab-specific focusing

        Returns:
            True if focus was successful, False otherwise
        """
        # iTerm2: Focus specific tab by session name
        if app_name == "iTerm" and session_name:
            if _focus_iterm_tab(session_name):
                return True

        # Terminal.app: Focus window by name
        if app_name == "Terminal" and session_name:
            if _focus_terminal_window(session_name):
                return True

        # Fallback: Just activate the app
        app_escaped = _escape_applescript_string(app_name)
        script = f'tell application "{app_escaped}" to activate'
        return _run_osascript(script)


class MacOSFocusDetector:
    """Detect if macOS terminal is currently focused."""

    def is_focused(self, app_name: str, session_name: str | None = None) -> bool:
        """Check if the terminal application is currently focused.

        Args:
            app_name: Name of the application to check
            session_name: Optional tmux session name for tab-specific check

        Returns:
            True if the app (and optionally session) is focused, False otherwise
        """
        # Step 1: Check if app is frontmost
        if not self._is_app_frontmost(app_name):
            return False

        # Step 2: For iTerm/Terminal, check if correct tab is focused
        if session_name:
            if app_name == "iTerm":
                return self._is_iterm_session_focused(session_name)
            if app_name == "Terminal":
                return self._is_terminal_window_focused(session_name)

        return True  # App is frontmost, that's enough for other apps

    def _is_app_frontmost(self, app_name: str) -> bool:
        """Check if the given app is the frontmost application.

        Args:
            app_name: Name of the application to check

        Returns:
            True if the app is frontmost, False otherwise
        """
        script = '''
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    return frontApp
end tell
'''
        result = _run_osascript_output(script)
        if result:
            return app_name.lower() in result.lower()
        return False

    def _is_iterm_session_focused(self, session_name: str) -> bool:
        """Check if the iTerm session with given name is focused.

        Args:
            session_name: The tmux session name to check

        Returns:
            True if the session is focused, False otherwise
        """
        session_escaped = _escape_applescript_string(session_name)
        script = f'''
tell application "iTerm"
    if (count of windows) = 0 then return false
    set currentSession to current session of current tab of current window
    return name of currentSession contains "{session_escaped}"
end tell
'''
        result = _run_osascript_output(script)
        return result == "true"

    def _is_terminal_window_focused(self, session_name: str) -> bool:
        """Check if the Terminal window with given name is focused.

        Args:
            session_name: The tmux session name to check

        Returns:
            True if the window is focused, False otherwise
        """
        session_escaped = _escape_applescript_string(session_name)
        script = f'''
tell application "Terminal"
    if (count of windows) = 0 then return false
    return name of front window contains "{session_escaped}"
end tell
'''
        result = _run_osascript_output(script)
        return result == "true"
