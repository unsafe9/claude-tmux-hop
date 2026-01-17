"""Installation logic for claude-tmux-hop."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .paths import (
    find_plugin_path,
    find_tpm_path,
    get_active_tmux_config,
    get_plugin_install_dir,
)


def detect_environment() -> dict[str, Any]:
    """Detect installation environment.

    Returns:
        Dictionary with detection results for tmux, claude, tpm, fzf, and in_tmux.
    """
    env: dict[str, Any] = {
        "tmux": {"installed": False, "version": None},
        "claude": {"installed": False, "version": None},
        "tpm": {"installed": False, "path": None},
        "fzf": {"installed": False},
        "in_tmux": "TMUX" in os.environ,
    }

    # Check tmux
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            env["tmux"]["installed"] = True
            env["tmux"]["version"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check claude CLI
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            env["claude"]["installed"] = True
            env["claude"]["version"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check TPM (supports XDG and custom paths)
    tpm_path = find_tpm_path()
    env["tpm"]["installed"] = tpm_path is not None
    env["tpm"]["path"] = str(tpm_path) if tpm_path else None

    # Check fzf
    env["fzf"]["installed"] = shutil.which("fzf") is not None

    return env


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

    if tmux_conf_path.exists():
        content = tmux_conf_path.read_text()
        if plugin_line in content or "claude-tmux-hop" in content:
            print(f"  Plugin already in {tmux_conf_path}")
            return True

    # Append plugin line
    with open(tmux_conf_path, "a") as f:
        f.write(f"\n# Claude Tmux Hop\n{plugin_line}\n")

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

    # Find the package installation path
    import claude_tmux_hop

    package_path = Path(claude_tmux_hop.__file__).parent.parent.parent

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
    try:
        # Add marketplace
        if not quiet:
            print("  Adding marketplace...")
        result = subprocess.run(
            ["claude", "plugin", "marketplace", "add", "unsafe9/claude-tmux-hop"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and "already" not in result.stderr.lower():
            print(f"  Warning: {result.stderr.strip()}")

        # Install plugin
        if not quiet:
            print("  Installing plugin...")
        result = subprocess.run(
            ["claude", "plugin", "install", "claude-tmux-hop"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and "already" not in result.stderr.lower():
            print(f"  Error: {result.stderr.strip()}")
            return False

        if not quiet:
            print("  Claude Code plugin installed")
        return True
    except FileNotFoundError:
        print("  Error: claude command not found")
        return False
    except subprocess.TimeoutExpired:
        print("  Error: command timed out")
        return False


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
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "claude-tmux-hop" in result.stdout:
            results["claude_plugin"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

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
        try:
            if not quiet:
                print(f"  Updating {plugin_dir} via git pull...")
            result = subprocess.run(
                ["git", "-C", str(plugin_dir), "pull", "--ff-only"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                if not quiet:
                    output = result.stdout.strip()
                    if "Already up to date" in output:
                        print("  Already up to date")
                    else:
                        print(f"  Updated: {output}")
                return True
            else:
                if not quiet:
                    print(f"  Error: {result.stderr.strip()}")
                return False
        except subprocess.TimeoutExpired:
            if not quiet:
                print("  Error: git pull timed out")
            return False
    elif plugin_dir.is_symlink():
        # Symlink installation - uvx handles updates
        if not quiet:
            print("  Symlink installation - update via: uvx claude-tmux-hop@latest")
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
    try:
        if not quiet:
            print("  Updating Claude Code plugin...")
        result = subprocess.run(
            ["claude", "plugin", "update", "claude-tmux-hop"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if not quiet:
                output = result.stdout.strip() or "Updated successfully"
                print(f"  {output}")
            return True
        else:
            # Check if not installed
            if "not installed" in result.stderr.lower() or "not found" in result.stderr.lower():
                if not quiet:
                    print("  Plugin not installed. Run: uvx claude-tmux-hop install")
                return False
            if not quiet:
                print(f"  Error: {result.stderr.strip()}")
            return False
    except FileNotFoundError:
        if not quiet:
            print("  Error: claude command not found")
        return False
    except subprocess.TimeoutExpired:
        if not quiet:
            print("  Error: command timed out")
        return False
