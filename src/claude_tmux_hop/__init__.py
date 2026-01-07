"""Claude Tmux Hop - Hop between Claude Code sessions in tmux panes."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("claude-tmux-hop")
except PackageNotFoundError:
    __version__ = "0.0.0"  # Fallback for development/editable installs
