"""Environment checking and diagnostics for claude-tmux-hop."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .paths import find_plugin_path, find_tpm_path, plugin_in_config

# Environment check constants
COMMAND_TIMEOUT = 5
COMMAND_TIMEOUT_LONG = 10
MIN_PYTHON_VERSION = (3, 10)
MAX_VERSION_DISPLAY_LENGTH = 50


@dataclass
class CheckResult:
    """Result of an environment check."""

    name: str
    ok: bool
    version: str | None = None
    message: str | None = None
    required: bool = True


def check_tmux() -> CheckResult:
    """Check tmux installation and version."""
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        if result.returncode != 0:
            return CheckResult("tmux", False, message="Command failed")

        version_str = result.stdout.strip()
        # Parse "tmux 3.2a" -> (3, 2)
        match = re.search(r"(\d+)\.(\d+)", version_str)
        if match:
            major, minor = int(match.group(1)), int(match.group(2))
            if (major, minor) < (3, 0):
                return CheckResult(
                    "tmux",
                    False,
                    version_str,
                    f"Requires 3.0+, found {major}.{minor}",
                )

        return CheckResult("tmux", True, version_str)
    except FileNotFoundError:
        return CheckResult("tmux", False, message="Not installed")
    except subprocess.TimeoutExpired:
        return CheckResult("tmux", False, message="Command timed out")


def check_python() -> CheckResult:
    """Check Python version."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < MIN_PYTHON_VERSION:
        return CheckResult("python", False, version, "Requires 3.10+")
    return CheckResult("python", True, version)


def check_claude_cli() -> CheckResult:
    """Check Claude Code CLI."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            # Truncate long version strings
            if len(version) > MAX_VERSION_DISPLAY_LENGTH:
                version = version[:MAX_VERSION_DISPLAY_LENGTH - 3] + "..."
            return CheckResult("claude", True, version)
        return CheckResult("claude", False, message="Command failed")
    except FileNotFoundError:
        return CheckResult("claude", False, message="Not installed")
    except subprocess.TimeoutExpired:
        return CheckResult("claude", False, message="Command timed out")


def check_tpm() -> CheckResult:
    """Check TPM installation."""
    tpm_path = find_tpm_path()
    if tpm_path:
        return CheckResult("tpm", True, message=str(tpm_path), required=False)
    return CheckResult("tpm", False, message="Not found (optional)", required=False)


def check_fzf() -> CheckResult:
    """Check fzf installation."""
    path = shutil.which("fzf")
    if path:
        return CheckResult("fzf", True, message=path, required=False)
    return CheckResult(
        "fzf",
        False,
        message="Not found (picker will use menu fallback)",
        required=False,
    )


def check_claude_plugin() -> CheckResult:
    """Check if Claude Code plugin is installed."""
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_LONG,
        )
        if "claude-tmux-hop" in result.stdout:
            return CheckResult("claude-plugin", True, message="Installed", required=False)
        return CheckResult("claude-plugin", False, message="Not installed", required=False)
    except FileNotFoundError:
        return CheckResult("claude-plugin", False, message="claude CLI not available", required=False)
    except subprocess.TimeoutExpired:
        return CheckResult("claude-plugin", False, message="Command timed out", required=False)


def check_tmux_plugin() -> CheckResult:
    """Check if tmux plugin is installed."""
    # Check plugin directory (supports XDG, custom paths, traditional)
    plugin_path = find_plugin_path("claude-tmux-hop")
    if plugin_path:
        return CheckResult("tmux-plugin", True, message=str(plugin_path), required=False)

    # Check tmux config files for plugin line
    config_path = plugin_in_config("claude-tmux-hop")
    if config_path:
        return CheckResult("tmux-plugin", True, message=f"In {config_path}", required=False)

    return CheckResult("tmux-plugin", False, message="Not installed", required=False)


def run_all_checks() -> list[CheckResult]:
    """Run all environment checks."""
    return [
        check_python(),
        check_tmux(),
        check_claude_cli(),
        check_tpm(),
        check_fzf(),
        check_tmux_plugin(),
        check_claude_plugin(),
    ]


def format_results(results: list[CheckResult], use_json: bool = False) -> str:
    """Format check results for display.

    Args:
        results: List of check results.
        use_json: If True, output as JSON.

    Returns:
        Formatted string.
    """
    if use_json:
        return json.dumps(
            [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "version": r.version,
                    "message": r.message,
                    "required": r.required,
                }
                for r in results
            ],
            indent=2,
        )

    lines = []
    for r in results:
        if r.ok:
            icon = "OK"
        elif not r.required:
            icon = "WARN"
        else:
            icon = "FAIL"
        detail = r.version or r.message or ""
        lines.append(f"  [{icon:4}] {r.name}: {detail}")

    return "\n".join(lines)
