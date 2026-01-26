"""Centralized tmux path resolution for claude-tmux-hop.

Handles XDG Base Directory support, oh-my-tmux, and custom TPM paths.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_xdg_config_home() -> Path:
    """Get XDG_CONFIG_HOME, defaulting to ~/.config."""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def get_tmux_config_paths() -> list[Path]:
    """Return all possible tmux config file paths in priority order.

    tmux searches in this order (first found wins):
    1. ~/.tmux.conf (legacy, most common)
    2. $XDG_CONFIG_HOME/tmux/tmux.conf
    3. ~/.config/tmux/tmux.conf (XDG default)

    Also includes oh-my-tmux user config location.
    """
    xdg_config = get_xdg_config_home()

    return [
        Path.home() / ".tmux.conf",
        xdg_config / "tmux" / "tmux.conf",
        Path.home() / ".config" / "tmux" / "tmux.conf",
        # Oh-my-tmux user config (plugins go here)
        Path.home() / ".tmux.conf.local",
        xdg_config / "tmux" / "tmux.conf.local",
    ]


def get_active_tmux_config() -> Path | None:
    """Find the active tmux configuration file.

    Returns the first existing config file from the search order.
    """
    for path in get_tmux_config_paths():
        if path.exists():
            return path
    return None


def get_tpm_env_path() -> Path | None:
    """Get TPM path from tmux environment variable.

    TPM sets TMUX_PLUGIN_MANAGER_PATH when initialized.
    This is the most reliable detection method when inside tmux.
    """
    if "TMUX" not in os.environ:
        return None

    try:
        result = subprocess.run(
            ["tmux", "show-environment", "-g", "TMUX_PLUGIN_MANAGER_PATH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output format: "TMUX_PLUGIN_MANAGER_PATH=/path/to/plugins"
            line = result.stdout.strip()
            if "=" in line and not line.startswith("-"):
                path_str = line.split("=", 1)[1].strip()
                # Expand ~ if present
                path_str = os.path.expanduser(path_str)
                # Remove trailing slash for consistency
                path = Path(path_str.rstrip("/"))
                if path.exists():
                    return path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def get_tpm_plugin_paths() -> list[Path]:
    """Return all possible TPM plugin directory paths in priority order.

    Checks:
    1. TMUX_PLUGIN_MANAGER_PATH environment (if in tmux)
    2. XDG config location
    3. Traditional ~/.tmux/plugins/
    """
    paths: list[Path] = []

    # Check tmux environment variable first
    env_path = get_tpm_env_path()
    if env_path:
        paths.append(env_path)

    # XDG location
    xdg_config = get_xdg_config_home()
    paths.append(xdg_config / "tmux" / "plugins")

    # XDG default (if XDG_CONFIG_HOME not set)
    paths.append(Path.home() / ".config" / "tmux" / "plugins")

    # Traditional location (most common)
    paths.append(Path.home() / ".tmux" / "plugins")

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        resolved = p.resolve() if p.exists() else p
        if resolved not in seen:
            seen.add(resolved)
            unique.append(p)

    return unique


def find_tpm_path() -> Path | None:
    """Find TPM installation directory.

    Returns the first existing TPM path from the search order.
    """
    for plugin_dir in get_tpm_plugin_paths():
        tpm_path = plugin_dir / "tpm"
        if tpm_path.exists():
            return tpm_path
    return None


def find_plugin_path(plugin_name: str) -> Path | None:
    """Find a specific plugin installation directory.

    Args:
        plugin_name: Name of the plugin (e.g., "claude-tmux-hop")

    Returns:
        Path to the plugin directory if found, None otherwise.
    """
    for plugin_dir in get_tpm_plugin_paths():
        plugin_path = plugin_dir / plugin_name
        if plugin_path.exists():
            return plugin_path
    return None


def get_plugin_install_dir() -> Path:
    """Get the directory where plugins should be installed.

    Uses the first existing plugin directory, or falls back to traditional.
    """
    # If TPM is installed somewhere, use that location
    tpm = find_tpm_path()
    if tpm:
        return tpm.parent

    # Check if any plugin directory exists
    for plugin_dir in get_tpm_plugin_paths():
        if plugin_dir.exists():
            return plugin_dir

    # Default to traditional location
    return Path.home() / ".tmux" / "plugins"


def plugin_in_config(plugin_name: str) -> Path | None:
    """Check if a plugin is referenced in any tmux config file.

    Args:
        plugin_name: Name of the plugin to search for

    Returns:
        Path to the config file containing the plugin, or None.
    """
    for config_path in get_tmux_config_paths():
        if config_path.exists():
            try:
                content = config_path.read_text()
                if plugin_name in content:
                    return config_path
            except (OSError, UnicodeDecodeError):
                continue
    return None
