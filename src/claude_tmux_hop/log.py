"""Logging configuration for claude-tmux-hop."""

from __future__ import annotations

import logging
import os
from pathlib import Path

# Log file location: ~/.local/state/claude-tmux-hop/hop.log
LOG_DIR = Path.home() / ".local" / "state" / "claude-tmux-hop"
LOG_FILE = LOG_DIR / "hop.log"

# Module-level logger
_logger: logging.Logger | None = None


def get_logger() -> logging.Logger:
    """Get or create the logger instance."""
    global _logger
    if _logger is not None:
        return _logger

    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Configure logger
    _logger = logging.getLogger("claude-tmux-hop")
    _logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times
    if not _logger.handlers:
        # File handler with detailed format
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setLevel(logging.DEBUG)

        # Format: timestamp | pane | project | level | message
        formatter = logging.Formatter(
            "%(asctime)s | %(pane)-6s | %(project)-20s | %(levelname)-5s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        _logger.addHandler(handler)

    return _logger


class PaneLogAdapter(logging.LoggerAdapter):
    """Logger adapter that includes pane ID and project in all log messages."""

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        # Add pane and project to extra if not present
        kwargs.setdefault("extra", {})
        kwargs["extra"].setdefault("pane", self.extra.get("pane", "?"))
        kwargs["extra"].setdefault("project", self.extra.get("project", "?"))
        return msg, kwargs


def get_pane_logger(pane_id: str | None = None) -> PaneLogAdapter:
    """Get a logger that includes pane ID and project in messages.

    Args:
        pane_id: The pane ID to include, or None to use TMUX_PANE env var
    """
    if pane_id is None:
        pane_id = os.environ.get("TMUX_PANE", "?")

    # Get project name from current working directory
    project = Path.cwd().name

    logger = get_logger()
    return PaneLogAdapter(logger, {"pane": pane_id, "project": project})


def log_cli_call(command: str, args: dict | None = None) -> None:
    """Log a CLI command invocation.

    Args:
        command: The command name (e.g., "init", "register")
        args: Command arguments as a dict
    """
    log = get_pane_logger()
    args_str = " ".join(f"{k}={v}" for k, v in (args or {}).items())
    log.info(f"CLI {command} {args_str}".strip())


def log_tmux_call(command: str, args: tuple, result: str | None = None) -> None:
    """Log a tmux command execution.

    Args:
        command: The tmux command (e.g., "set-option")
        args: Command arguments
        result: Command output (optional)
    """
    log = get_pane_logger()
    args_str = " ".join(str(a) for a in args)
    if result:
        log.debug(f"tmux {command} {args_str} -> {result[:100]}")
    else:
        log.debug(f"tmux {command} {args_str}")


def log_error(message: str, exc: Exception | None = None) -> None:
    """Log an error message.

    Args:
        message: Error description
        exc: Optional exception
    """
    log = get_pane_logger()
    if exc:
        log.error(f"{message}: {exc}")
    else:
        log.error(message)


def log_info(message: str) -> None:
    """Log an info message."""
    log = get_pane_logger()
    log.info(message)


def log_debug(message: str) -> None:
    """Log a debug message."""
    log = get_pane_logger()
    log.debug(message)
