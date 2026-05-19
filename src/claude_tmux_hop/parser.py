"""Argument parser setup for claude-tmux-hop CLI."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from . import __version__
from .priority import VALID_CYCLE_MODES, VALID_STATES

if TYPE_CHECKING:
    from typing import Callable
    from argparse import Namespace

    CommandHandler = Callable[[Namespace], int]


def create_parser(
    *,
    cmd_register: CommandHandler,
    cmd_clear: CommandHandler,
    cmd_cycle: CommandHandler,
    cmd_back: CommandHandler,
    cmd_picker_data: CommandHandler,
    cmd_switch: CommandHandler,
    cmd_list: CommandHandler,
    cmd_discover: CommandHandler,
    cmd_prune: CommandHandler,
    cmd_status: CommandHandler,
    cmd_inbox: CommandHandler,
    cmd_inbox_clear: CommandHandler,
    cmd_install: CommandHandler,
    cmd_update: CommandHandler,
    cmd_doctor: CommandHandler,
    cmd_spawn_task: CommandHandler,
    cmd_send_prompt: CommandHandler,
    cmd_conductor: CommandHandler,
    cmd_conductor_context: CommandHandler,
    cmd_conductor_prompt_context: CommandHandler,
) -> argparse.ArgumentParser:
    """Create and configure the argument parser.

    Args:
        cmd_*: Command handler functions to attach to subparsers

    Returns:
        Configured ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="claude-tmux-hop",
        description="Hop between Claude Code sessions in tmux panes",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # register command
    register_parser = subparsers.add_parser(
        "register",
        help="Register current pane with a state",
    )
    register_parser.add_argument(
        "--state",
        "-s",
        required=True,
        choices=VALID_STATES,
        help="State to register",
    )
    register_parser.add_argument(
        "--task",
        "-t",
        help="Manual task summary override (skips transcript-based auto-derivation)",
    )
    register_parser.set_defaults(func=cmd_register)

    # clear command
    clear_parser = subparsers.add_parser(
        "clear",
        help="Clear hop state from current pane",
    )
    clear_parser.set_defaults(func=cmd_clear)

    # cycle command
    cycle_parser = subparsers.add_parser(
        "cycle",
        help="Cycle to next pane in priority order",
    )
    cycle_parser.add_argument(
        "--pane",
        "-p",
        help="Current pane ID (passed by tmux keybinding)",
    )
    cycle_parser.add_argument(
        "--mode",
        "-m",
        choices=VALID_CYCLE_MODES,
        default="priority",
        help="Cycle mode: 'priority' cycles within highest-priority group, 'flat' cycles through all panes",
    )
    cycle_parser.set_defaults(func=cmd_cycle)

    # back command
    back_parser = subparsers.add_parser(
        "back",
        help="Jump back to the previous pane",
    )
    back_parser.set_defaults(func=cmd_back)

    # picker-data command (internal)
    picker_data_parser = subparsers.add_parser(
        "picker-data",
        help="Output pane data for fzf (internal)",
    )
    picker_data_parser.set_defaults(func=cmd_picker_data)

    # switch command (internal)
    switch_parser = subparsers.add_parser(
        "switch",
        help="Switch to a specific pane (internal)",
    )
    switch_parser.add_argument(
        "--pane",
        "-p",
        required=True,
        help="Pane ID to switch to",
    )
    switch_parser.set_defaults(func=cmd_switch)

    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List all Claude Code panes",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (with git context per pane)",
    )
    list_parser.set_defaults(func=cmd_list)

    # discover command
    discover_parser = subparsers.add_parser(
        "discover",
        help="Discover and register existing Claude Code sessions",
    )
    discover_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be registered without making changes",
    )
    discover_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Re-register panes that are already registered",
    )
    discover_parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output except errors",
    )
    discover_parser.set_defaults(func=cmd_discover)

    # prune command
    prune_parser = subparsers.add_parser(
        "prune",
        help="Remove stale hop state from panes no longer running Claude Code",
    )
    prune_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be removed without making changes",
    )
    prune_parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output except errors",
    )
    prune_parser.set_defaults(func=cmd_prune)

    # status command
    status_parser = subparsers.add_parser(
        "status",
        help="Output status for tmux status bar",
    )
    status_parser.set_defaults(func=cmd_status)

    # inbox command (internal)
    inbox_parser = subparsers.add_parser(
        "inbox",
        help="Output notification inbox for display menu (internal)",
    )
    inbox_parser.set_defaults(func=cmd_inbox)

    # inbox-clear command
    inbox_clear_parser = subparsers.add_parser(
        "inbox-clear",
        help="Clear notification inbox",
    )
    inbox_clear_parser.set_defaults(func=cmd_inbox_clear)

    # --- Management commands ---

    # install command
    install_parser = subparsers.add_parser(
        "install",
        help="Install tmux and Claude Code plugins",
    )
    install_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Accept all prompts (non-interactive)",
    )
    install_parser.add_argument(
        "--component",
        choices=["all", "tmux", "claude"],
        default="all",
        help="Component to install (default: all)",
    )
    install_parser.add_argument(
        "--skip-tmux",
        action="store_true",
        help="Skip tmux plugin installation",
    )
    install_parser.add_argument(
        "--skip-claude",
        action="store_true",
        help="Skip Claude Code plugin installation",
    )
    install_parser.set_defaults(func=cmd_install)

    # update command
    update_parser = subparsers.add_parser(
        "update",
        help="Update installed plugins to latest version",
    )
    update_parser.add_argument(
        "--component",
        choices=["all", "tmux", "claude"],
        default="all",
        help="Component to update (default: all)",
    )
    update_parser.set_defaults(func=cmd_update)

    # doctor command
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check environment and dependencies",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    doctor_parser.set_defaults(func=cmd_doctor)

    # --- Conductor primitives ---

    spawn_task_parser = subparsers.add_parser(
        "spawn-task",
        help="Open a new tmux window running claude with a pre-submitted prompt",
    )
    spawn_task_parser.add_argument("--cwd", required=True, help="Working directory for the new window")
    spawn_task_parser.add_argument("--prompt", required=True, help="Prompt to send to claude")
    spawn_task_parser.add_argument("--session", required=True, help="Target tmux session (created if missing)")
    spawn_task_parser.add_argument("--window-name", default=None, help="Optional window name")
    spawn_task_parser.add_argument(
        "--no-switch",
        dest="switch",
        action="store_false",
        default=True,
        help="Do not switch the active client to the new window",
    )
    spawn_task_parser.set_defaults(func=cmd_spawn_task)

    send_prompt_parser = subparsers.add_parser(
        "send-prompt",
        help="Inject a prompt into an existing claude pane",
    )
    send_prompt_parser.add_argument("--pane", required=True, help="Target pane id (e.g., %%X)")
    send_prompt_parser.add_argument("--prompt", required=True, help="Prompt to inject")
    send_prompt_parser.add_argument(
        "--no-switch",
        dest="switch",
        action="store_false",
        default=True,
        help="Do not switch the active client to the target pane",
    )
    send_prompt_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Override the active-pane safety check",
    )
    send_prompt_parser.set_defaults(func=cmd_send_prompt)

    conductor_parser = subparsers.add_parser(
        "conductor",
        help="Open the conductor popup (fresh claude in the workbench), or refresh its CLAUDE.md",
    )
    conductor_mode_group = conductor_parser.add_mutually_exclusive_group()
    conductor_mode_group.add_argument(
        "--popup",
        dest="mode",
        action="store_const",
        const="popup",
        help="Open the conductor popup (default)",
    )
    conductor_mode_group.add_argument(
        "--update-instructions",
        dest="mode",
        action="store_const",
        const="update_instructions",
        help="Refresh the plugin-managed instructions in the workbench CLAUDE.md (preserves user content outside the <conductor-instructions> marker)",
    )
    conductor_mode_group.add_argument(
        "--kill",
        dest="mode",
        action="store_const",
        const="kill",
        help="Kill the conductor tmux session if it exists (idempotent — no-op if not running)",
    )
    conductor_parser.add_argument(
        "--respawn",
        action="store_true",
        default=False,
        help="With --popup, kill any existing conductor session before attaching (fresh claude, destructive of in-flight state)",
    )
    conductor_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="With --update-instructions, overwrite a CLAUDE.md that has no conductor marker (backs up old to CLAUDE.md.bak)",
    )
    conductor_parser.set_defaults(mode="popup", func=cmd_conductor)

    # conductor-context (internal — invoked by SessionStart hook)
    conductor_context_parser = subparsers.add_parser(
        "conductor-context",
        help="Emit SessionStart context JSON when running inside the conductor workbench (internal)",
    )
    conductor_context_parser.set_defaults(func=cmd_conductor_context)

    # conductor-prompt-context (internal — invoked by UserPromptSubmit hook)
    conductor_prompt_context_parser = subparsers.add_parser(
        "conductor-prompt-context",
        help="Emit UserPromptSubmit context JSON (fresh pane snapshot) when running inside the conductor workbench (internal)",
    )
    conductor_prompt_context_parser.set_defaults(func=cmd_conductor_prompt_context)

    return parser
