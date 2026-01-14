"""Windows notification and focus handlers using PowerShell."""

from __future__ import annotations

import subprocess

from .base import SUBPROCESS_TIMEOUT_LONG, PaneContext, switch_tmux_pane


class WindowsNotifier:
    """Send notifications on Windows using PowerShell Toast Notifications."""

    def send(self, title: str, message: str, on_click: PaneContext | None = None) -> bool:
        """Send a Windows 10+ toast notification via PowerShell.

        Args:
            title: Notification title
            message: Notification body
            on_click: Optional pane context for click-to-focus action
                     (not supported on Windows - requires protocol handler setup)

        Returns:
            True if notification was sent successfully

        Note:
            Click-to-focus is not implemented on Windows. Implementing this feature
            would require registering a custom URI protocol handler (e.g., tmux:)
            which is beyond the scope of this tool. The on_click parameter is
            accepted for API compatibility but ignored.
        """
        title_escaped = title.replace("'", "''")
        message_escaped = message.replace("'", "''")

        # Build simple toast XML (click-to-focus not supported on Windows)
        toast_xml = f'''
<toast>
    <visual>
        <binding template="ToastText02">
            <text id="1">{title_escaped}</text>
            <text id="2">{message_escaped}</text>
        </binding>
    </visual>
</toast>
'''

        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = @'
{toast_xml}
'@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Claude Code').Show($toast)
"""

        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT_LONG,
                check=False,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            return False


class WindowsFocusHandler:
    """Focus terminal windows on Windows using PowerShell COM automation."""

    def focus(self, app_name: str, session_name: str | None = None,
              pane_context: PaneContext | None = None) -> bool:
        """Bring an application to the foreground on Windows.

        Args:
            app_name: Name of the application (window title or process name)
            session_name: Optional tmux session name (unused on Windows)
            pane_context: Optional pane context for tmux navigation

        Returns:
            True if focus was successful

        Note:
            This uses WScript.Shell.AppActivate which may not work reliably with
            all terminal applications. Windows Terminal, ConEmu, and some other
            terminals have specific window management that may interfere with
            COM automation. If focus fails, the user may need to manually switch
            to the terminal window.
        """
        # First: focus the window
        focused = self._focus_window(app_name)

        # Then: switch tmux pane if context provided
        if focused and pane_context:
            switch_tmux_pane(pane_context)

        return focused

    def _focus_window(self, app_name: str) -> bool:
        """Bring application window to foreground using COM automation."""
        app_escaped = app_name.replace("'", "''")

        ps_script = f"""
$wshell = New-Object -ComObject WScript.Shell
$wshell.AppActivate('{app_escaped}')
"""

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                timeout=SUBPROCESS_TIMEOUT_LONG,
                check=False,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False


class WindowsFocusDetector:
    """Detect if Windows terminal window is currently focused."""

    def is_focused(self, app_name: str, session_name: str | None = None) -> bool:
        """Check if the terminal window is focused using PowerShell.

        Gets the foreground window title and checks if it matches.

        Args:
            app_name: Name of the application to check
            session_name: Optional session name to match in window title

        Returns:
            True if the terminal window is focused
        """
        ps_script = """
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public class Win32 {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
}
"@

$hwnd = [Win32]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 256
[void][Win32]::GetWindowText($hwnd, $sb, 256)
$sb.ToString()
"""

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_LONG,
                check=False,
            )
            if result.returncode == 0 and result.stdout:
                window_title = result.stdout.strip()
                search_term = session_name if session_name else app_name
                return search_term.lower() in window_title.lower()
        except (subprocess.SubprocessError, OSError):
            pass

        return False  # Assume not focused if detection fails
