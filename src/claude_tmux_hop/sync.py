"""Version synchronization logic for claude-tmux-hop."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def get_project_root() -> Path:
    """Find project root by looking for pyproject.toml.

    Traverses up from the package location to find the project root.

    Returns:
        Path to the project root.

    Raises:
        FileNotFoundError: If pyproject.toml cannot be found.
    """
    import claude_tmux_hop

    package_path = Path(claude_tmux_hop.__file__).parent

    # Try to find pyproject.toml going up
    for parent in [package_path] + list(package_path.parents):
        if (parent / "pyproject.toml").exists():
            return parent

    # Fallback to cwd
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists():
        return cwd

    raise FileNotFoundError("Could not find pyproject.toml in any parent directory")


def read_pyproject_version() -> str:
    """Read version from pyproject.toml.

    Returns:
        Version string.

    Raises:
        ValueError: If version cannot be found in pyproject.toml.
    """
    root = get_project_root()
    pyproject = root / "pyproject.toml"

    content = pyproject.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def read_plugin_versions() -> dict[str, str | None]:
    """Read current versions from plugin files.

    Returns:
        Dictionary with version from each plugin file, or None if not found.
    """
    root = get_project_root()
    versions: dict[str, str | None] = {
        "plugin.json": None,
        "marketplace.json": None,
    }

    plugin_json = root / ".claude-plugin" / "plugin.json"
    if plugin_json.exists():
        try:
            data = json.loads(plugin_json.read_text())
            versions["plugin.json"] = data.get("version")
        except (json.JSONDecodeError, KeyError):
            pass

    marketplace_json = root / ".claude-plugin" / "marketplace.json"
    if marketplace_json.exists():
        try:
            data = json.loads(marketplace_json.read_text())
            plugins = data.get("plugins", [])
            if plugins:
                versions["marketplace.json"] = plugins[0].get("version")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

    return versions


def update_json_version(
    path: Path,
    version: str,
    json_path: list[str | int],
    dry_run: bool = False,
) -> str | None:
    """Update version in a JSON file at the given path.

    Args:
        path: Path to JSON file.
        version: New version string.
        json_path: Path to version field (e.g., ["version"] or ["plugins", 0, "version"]).
        dry_run: If True, don't modify file.

    Returns:
        Diff string if changed, None if unchanged.
    """
    content = path.read_text()
    data = json.loads(content)

    # Navigate to parent of version field
    obj: Any = data
    for key in json_path[:-1]:
        obj = obj[key]

    last_key = json_path[-1]
    old_version = obj[last_key]
    if old_version == version:
        return None

    obj[last_key] = version
    new_content = json.dumps(data, indent=2) + "\n"

    if not dry_run:
        path.write_text(new_content)

    return f"{path.name}: {old_version} -> {version}"


def sync_versions(
    dry_run: bool = False,
    check_only: bool = False,
) -> tuple[list[str], bool]:
    """Synchronize all version files.

    Args:
        dry_run: If True, show what would change without modifying files.
        check_only: If True, only check for sync issues (implies dry_run).

    Returns:
        Tuple of (list of changes, bool success).
        Success is True if all versions are in sync (when check_only)
        or if sync completed (otherwise).
    """
    if check_only:
        dry_run = True

    root = get_project_root()
    version = read_pyproject_version()
    changes: list[str] = []

    # Update plugin.json
    plugin_json = root / ".claude-plugin" / "plugin.json"
    if plugin_json.exists():
        diff = update_json_version(plugin_json, version, ["version"], dry_run)
        if diff:
            changes.append(diff)

    # Update marketplace.json
    marketplace_json = root / ".claude-plugin" / "marketplace.json"
    if marketplace_json.exists():
        diff = update_json_version(marketplace_json, version, ["plugins", 0, "version"], dry_run)
        if diff:
            changes.append(diff)

    success = not check_only or len(changes) == 0
    return changes, success
