"""Installation logic for claude-tmux-hop."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .doctor import check_claude_cli, check_fzf, check_tmux, check_tpm
from .paths import (
    find_plugin_path,
    get_active_tmux_config,
    get_plugin_install_dir,
)


DEFAULT_COMMAND_TIMEOUT = 30
PLUGIN_LIST_TIMEOUT = 10


@dataclass
class CommandResult:
    """Result of running a command."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str = ""


def _run_command(cmd: list[str], timeout: int = DEFAULT_COMMAND_TIMEOUT) -> CommandResult:
    """Run a command with standard error handling.

    Args:
        cmd: Command and arguments to run.
        timeout: Timeout in seconds.

    Returns:
        CommandResult with success status and output.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return CommandResult(
            success=result.returncode == 0,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
    except FileNotFoundError:
        return CommandResult(success=False, error=f"{cmd[0]} command not found")
    except subprocess.TimeoutExpired:
        return CommandResult(success=False, error="command timed out")


def detect_environment() -> dict[str, Any]:
    """Detect installation environment.

    Returns:
        Dictionary with detection results for tmux, claude, tpm, fzf, and in_tmux.
    """
    # Use doctor.py checks to avoid duplication
    tmux_result = check_tmux()
    claude_result = check_claude_cli()
    tpm_result = check_tpm()
    fzf_result = check_fzf()

    return {
        "tmux": {"installed": tmux_result.ok, "version": tmux_result.version},
        "claude": {"installed": claude_result.ok, "version": claude_result.version},
        "tpm": {"installed": tpm_result.ok, "path": tpm_result.message},
        "fzf": {"installed": fzf_result.ok},
        "in_tmux": "TMUX" in os.environ,
    }


def prompt_user(message: str, default: bool = True) -> bool:
    """Interactive prompt with default value.

    Args:
        message: The prompt message to display.
        default: Default value if user just presses Enter.

    Returns:
        True for yes, False for no.
    """
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        response = input(message + suffix).strip().lower()
        if not response:
            return default
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def install_tmux_plugin_tpm(tmux_conf_path: Path | None = None) -> bool:
    """Add TPM plugin line to tmux.conf.

    Args:
        tmux_conf_path: Path to the tmux.conf file. If None, auto-detects.

    Returns:
        True if successful, False otherwise.
    """
    # Auto-detect config path if not provided
    if tmux_conf_path is None:
        tmux_conf_path = get_active_tmux_config()
        if tmux_conf_path is None:
            # Default to traditional location
            tmux_conf_path = Path.home() / ".tmux.conf"

    plugin_line = "set -g @plugin 'unsafe9/claude-tmux-hop'"

    try:
        if tmux_conf_path.exists():
            content = tmux_conf_path.read_text()
            if plugin_line in content or "claude-tmux-hop" in content:
                print(f"  Plugin already in {tmux_conf_path}")
                return True
            # Check for oh-my-tmux markers - prefer .tmux.conf.local instead
            if "oh-my-tmux" in content or "/.tmux/.tmux.conf" in content:
                tmux_conf_path = Path.home() / ".tmux.conf.local"
                if tmux_conf_path.exists():
                    local_content = tmux_conf_path.read_text()
                    if plugin_line in local_content or "claude-tmux-hop" in local_content:
                        print(f"  Plugin already in {tmux_conf_path}")
                        return True

        # Ensure parent directory exists
        tmux_conf_path.parent.mkdir(parents=True, exist_ok=True)

        # Append plugin line
        with open(tmux_conf_path, "a") as f:
            f.write(f"\n# Claude Tmux Hop\n{plugin_line}\n")
    except PermissionError:
        print(f"  Error: Permission denied writing to {tmux_conf_path}")
        return False
    except OSError as e:
        print(f"  Error writing config: {e}")
        return False

    print(f"  Added to {tmux_conf_path}")
    print(f"  Run 'prefix + I' in tmux to install, or reload: tmux source {tmux_conf_path}")
    return True


def install_tmux_plugin_manual(plugin_dir: Path | None = None) -> bool:
    """Install via symlink for non-TPM users.

    Args:
        plugin_dir: Directory to install the plugin into. If None, auto-detects.

    Returns:
        True if successful, False otherwise.
    """
    # Auto-detect plugin directory if not provided
    if plugin_dir is None:
        plugin_dir = get_plugin_install_dir()

    # Find the project root (where hop.tmux lives)
    import claude_tmux_hop

    package_dir = Path(claude_tmux_hop.__file__).parent
    # Try source layout first: src/claude_tmux_hop/ -> project root is .parent.parent
    # Then pip layout: site-packages/claude_tmux_hop/ -> project root is .parent
    package_path = None
    for levels in (2, 1, 3):
        candidate = package_dir
        for _ in range(levels):
            candidate = candidate.parent
        if (candidate / "hop.tmux").exists():
            package_path = candidate
            break

    if package_path is None:
        print("  Error: Could not locate project root (hop.tmux not found)")
        return False

    target = plugin_dir / "claude-tmux-hop"

    if target.exists():
        print(f"  Plugin directory already exists: {target}")
        return True

    plugin_dir.mkdir(parents=True, exist_ok=True)

    try:
        target.symlink_to(package_path)
        print(f"  Created symlink: {target} -> {package_path}")
        print(f"  Add to tmux.conf: run-shell '{target}/hop.tmux'")
        return True
    except OSError as e:
        print(f"  Error creating symlink: {e}")
        return False


def install_claude_plugin(quiet: bool = False) -> bool:
    """Install Claude Code plugin via CLI.

    Args:
        quiet: If True, suppress informational output.

    Returns:
        True if successful, False otherwise.
    """
    # Add marketplace
    if not quiet:
        print("  Adding marketplace...")
    result = _run_command(["claude", "plugin", "marketplace", "add", "unsafe9/claude-tmux-hop"])
    if result.error:
        print(f"  Error: {result.error}")
        return False
    if not result.success and "already" not in result.stderr.lower():
        print(f"  Warning: {result.stderr}")

    # Install plugin
    if not quiet:
        print("  Installing plugin...")
    result = _run_command(["claude", "plugin", "install", "claude-tmux-hop"])
    if result.error:
        print(f"  Error: {result.error}")
        return False
    if not result.success and "already" not in result.stderr.lower():
        print(f"  Error: {result.stderr}")
        return False

    if not quiet:
        print("  Claude Code plugin installed")
    return True


def verify_installation() -> dict[str, bool]:
    """Verify all components are installed correctly.

    Returns:
        Dictionary with verification results.
    """
    results: dict[str, bool] = {
        "tmux_plugin": False,
        "claude_plugin": False,
    }

    # Check tmux plugin (supports XDG, custom paths, traditional)
    plugin_path = find_plugin_path("claude-tmux-hop")
    if plugin_path:
        results["tmux_plugin"] = True

    # Check Claude plugin
    result = _run_command(["claude", "plugin", "list"], timeout=PLUGIN_LIST_TIMEOUT)
    if result.success and "claude-tmux-hop" in result.stdout:
        results["claude_plugin"] = True

    return results


def update_tmux_plugin(quiet: bool = False) -> bool:
    """Update tmux plugin via TPM or git pull.

    Args:
        quiet: If True, suppress informational output.

    Returns:
        True if successful, False otherwise.
    """
    # Find plugin using path detection (supports XDG, custom paths)
    plugin_dir = find_plugin_path("claude-tmux-hop")

    if not plugin_dir:
        if not quiet:
            print("  Tmux plugin not installed")
        return False

    # Check if it's a git repo (TPM-managed)
    git_dir = plugin_dir / ".git"
    if git_dir.exists():
        if not quiet:
            print(f"  Updating {plugin_dir} via git pull...")
        result = _run_command(["git", "-C", str(plugin_dir), "pull", "--ff-only"])
        if result.error:
            if not quiet:
                print(f"  Error: {result.error}")
            return False
        if result.success:
            if not quiet:
                if "Already up to date" in result.stdout:
                    print("  Already up to date")
                else:
                    print(f"  Updated: {result.stdout}")
            return True
        else:
            if not quiet:
                print(f"  Error: {result.stderr}")
            return False
    elif plugin_dir.is_symlink():
        # Symlink installation - update via git in source directory
        if not quiet:
            print("  Symlink installation - update via git pull in source directory")
        return True
    else:
        if not quiet:
            print("  Unknown installation type")
        return False


def update_claude_plugin(quiet: bool = False) -> bool:
    """Update Claude Code plugin via CLI.

    Args:
        quiet: If True, suppress informational output.

    Returns:
        True if successful, False otherwise.
    """
    if not quiet:
        print("  Updating Claude Code plugin...")
    result = _run_command(["claude", "plugin", "update", "claude-tmux-hop"])

    if result.error:
        if not quiet:
            print(f"  Error: {result.error}")
        return False

    if result.success:
        if not quiet:
            output = result.stdout or "Updated successfully"
            print(f"  {output}")
        return True
    else:
        # Check if not installed
        if "not installed" in result.stderr.lower() or "not found" in result.stderr.lower():
            if not quiet:
                print("  Plugin not installed. Install via: claude plugin install claude-tmux-hop")
            return False
        if not quiet:
            print(f"  Error: {result.stderr}")
        return False
