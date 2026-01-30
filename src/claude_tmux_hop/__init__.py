"""Claude Tmux Hop - Hop between Claude Code sessions in tmux panes."""

import re
from pathlib import Path


def _get_version() -> str:
    """Get version from pyproject.toml."""
    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    if pyproject.exists():
        match = re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.MULTILINE)
        if match:
            return match.group(1)
    return "0.0.0"


__version__ = _get_version()
