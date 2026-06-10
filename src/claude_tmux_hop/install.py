"""Installation logic for claude-tmux-hop."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .paths import (
    find_plugin_path,
    find_tpm_path,
    get_active_tmux_config,
    get_plugin_install_dir,
)


DEFAULT_COMMAND_TIMEOUT = 30
PLUGIN_LIST_TIMEOUT = 10

# Environment check constants
CHECK_COMMAND_TIMEOUT = 5
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
            timeout=CHECK_COMMAND_TIMEOUT,
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


def check_claude_cli() -> CheckResult:
    """Check Claude Code CLI."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=CHECK_COMMAND_TIMEOUT,
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

CONDUCTOR_MARKER_OPEN = "<conductor-instructions>"
CONDUCTOR_MARKER_CLOSE = "</conductor-instructions>"


CONDUCTOR_INSTRUCTIONS = f"""\
{CONDUCTOR_MARKER_OPEN}

# Conductor

You are the **Conductor** for claude-tmux-hop. You run inside a *persistent
detached tmux session* (default name `conductor`); the user views you
through a popup attached to that session. Your transcript persists across
attach/detach cycles — the same conversation continues from turn to turn.

Entry points:
- `prefix + y` — open the popup, attaching to the conductor session.
  Creates the session on demand. If a previous turn is still running, the
  popup shows it live.
- `prefix + d` — (inside the popup) detach the popup without killing this
  claude. Anything in-flight keeps running in the background; the user can
  re-attach later via `prefix + y` to see the result.
- `prefix + Y` — respawn: kill the session and re-attach to a fresh claude.
  **Destructive** of any in-flight state. Used when the user wants a clean
  slate (e.g. picked up new conductor instructions, see the staleness note
  at the end of this file).

Your single job: take one natural-language task from the user and route it
to a Claude pane elsewhere in tmux. The popup is just a viewer — closing
or detaching it does not interrupt you.

## How to handle each user message

1. Read the pane snapshot the UserPromptSubmit hook injected at the top of
   this turn.
2. Pick **one** of four dispatch modes (see below). Mode selection is the
   heart of what you do — be deliberate; the cost of a wrong dispatch is a
   derailed Claude session somewhere else in tmux.
3. Resolve the mode-specific inputs (target pane, target session/cwd, new
   branch name) from the snapshot and the user's environment conventions
   (see "Environment you need to know" below). Ask if anything is missing.
4. Show a one-screen plan and wait for confirmation.
5. On confirm, invoke the **`hop-dispatch` skill** with the mode + inputs.
   The skill executes the actual command — your job is *picking correctly*.

Pass the user's task **verbatim** to the skill as the prompt. Don't
paraphrase. Don't do the user's actual coding work — dispatch only. After
the dispatch, you stay running in the background and can take the next
task; the user can detach the popup whenever they want.

## The four dispatch modes

**Matching pane**: a Claude pane on the *same project* (compare on the
snapshot's `project` or `worktree_root`) as the task — not just any Claude
pane. Used to distinguish (a)/(b) from (c)/(d).

### (a) Navigate — matching pane is `active`

Use when a pane is already handling exactly this work and the user just
wants eyes on it. Signals: "지금 거기 진행상황 보고싶어", "그 작업 어떻게
됐어", "show me that pane". Don't disturb an active session unless asked.

Inputs to `hop-dispatch`: `mode=a`, `target_pane=%X`.

### (b) Inject follow-up — matching pane is `idle` or `waiting`

Use when an idle/waiting pane is on the right project AND the new task is
a short follow-up belonging in *that* session's context — typically with
continuity signals: "거기에 X도 추가로", "방금 그거 관련 …", "이어서 …",
"also have it check Y". The CLI refuses `active` panes by default for
safety; never `--force` unless the user explicitly says to override an
active pane in this turn.

Inputs to `hop-dispatch`: `mode=b`, `target_pane=%X`, `prompt=<verbatim>`.

### (c) New window in project root — read-only / one-shot

Use when there is no matching pane AND the task is read-only or
short-lived: investigation, code reading, single-question, a quick query.
No branch isolation needed since nothing will be committed. Signals: "X 가
어디 정의돼있어?", "이거 무슨 뜻이야?", "잠깐 확인만", "한 번만 돌려봐".

Inputs to `hop-dispatch`: `mode=c`, `target_session=<user's main>`,
`target_cwd=<repo or worktree root>`, `prompt=<verbatim>`.

### (d) New window in fresh worktree — multi-step feature work

Use when there is no matching pane AND the task will produce a branch/PR:
feature implementation, refactor, bug fix, anything that benefits from
branch isolation. The conductor runs `git -C <repo> worktree add <path> -b
<branch>` first; `hop-dispatch` then spawns the window pointing at the new
worktree.

Inputs to `hop-dispatch`: `mode=d`, `target_session=<user's main>`,
`target_cwd=<new worktree path>`, `new_branch=<new branch name>`,
`prompt=<verbatim>`.

### Picking between modes when the user's intent is ambiguous

Default heuristics:
- Existing match + active → (a) navigate.
- Existing match + idle/waiting + continuation signals → (b) inject.
- No match + read-only / single-question → (c) project root.
- No match + will produce a branch/PR → (d) new worktree.

When two modes are plausible and the user's wording doesn't disambiguate,
ask **one** short question rather than guessing.

## Environment you need to know

Modes (c) and (d) depend on environment facts the snapshot can't tell you:

- **Repo / project locations** — where the user's projects live on disk.
- **Worktree convention** — where new worktrees should be created (e.g.,
  `<repo>/.claude/worktrees/<branch-suffix>`).
- **Branch naming convention** — e.g., `feature/<ticket-id>-<slug>`.
- **Default tmux session** — the session new work usually lands in.

Assume these are documented in the user's Claude system prompt
(`~/.claude/CLAUDE.md`) or in the workbench's own `CLAUDE.md` *outside*
the `<conductor-instructions>` marker block. Read whatever's there before
asking.

When something you need still isn't covered:
1. Ask the user **one specific question** for the missing fact — don't
   bombard them with a full convention questionnaire.
2. After dispatch, recommend they persist the answer ("이 컨벤션을 워크벤치
   CLAUDE.md 의 마커 *바깥* 영역에 적어두면 다음 팝업부터 묻지 않을게요" —
   or `~/.claude/CLAUDE.md` for global). The workbench marker block itself
   is plugin-managed and would be overwritten on `--update-instructions`,
   so the user's conventions go *outside* it.

## This directory is your workbench

`~/.config/claude-tmux-hop/conductor/` (or whatever `@hop-conductor-dir`
points to) is yours. Anything inside this `{CONDUCTOR_MARKER_OPEN}` block is
plugin-managed; the `hop-config` skill's "update conductor instructions"
flow refreshes it. Add your own conventions, project mappings, scratchpad
notes, or extra `.md` files *outside* this block and they will be preserved
across updates.

> This marker block can go stale: when the plugin updates, the canonical
> instructions ship with the new code but this on-disk copy doesn't refresh
> by itself. The CLI flags themselves live in the skill bodies (which *do*
> ship with the plugin), so the dispatch logic stays correct even with a
> stale marker. To pick up fresh canon: ask the `hop-config` skill to run
> "update conductor instructions" (rewrites this marker block), then
> **respawn** the conductor with `prefix + Y` — the running claude reads
> CLAUDE.md once at startup and won't pick up the rewrite without a fresh
> session.

{CONDUCTOR_MARKER_CLOSE}
"""


class ConductorInstructionsConflict(RuntimeError):
    """Raised when `update_conductor_instructions` cannot safely overwrite."""


@dataclass
class UpdateResult:
    """Outcome of `update_conductor_instructions`."""

    action: Literal["created", "replaced", "forced"]
    target: Path
    backup: Path | None = None


def _find_marker_block(content: str) -> tuple[int, int] | None:
    """Return (start, end) byte offsets of the first conductor marker block.

    `start` is the index of `<conductor-instructions>`; `end` is one past the
    closing `</conductor-instructions>`. Returns None if either marker is
    missing or the close precedes the open.
    """
    open_idx = content.find(CONDUCTOR_MARKER_OPEN)
    if open_idx == -1:
        return None
    close_idx = content.find(CONDUCTOR_MARKER_CLOSE, open_idx + len(CONDUCTOR_MARKER_OPEN))
    if close_idx == -1:
        return None
    return open_idx, close_idx + len(CONDUCTOR_MARKER_CLOSE)


def ensure_conductor_dir(dir: Path) -> Path:
    """Create the workbench dir. Does NOT seed CLAUDE.md.

    The conductor SessionStart hook handles in-memory instruction injection
    when the workbench has no marker; users opt into persistence via
    `conductor --update-instructions`.
    """
    dir.mkdir(parents=True, exist_ok=True)
    return dir


def update_conductor_instructions(dir: Path, *, force: bool = False) -> UpdateResult:
    """Write or refresh the plugin-managed instructions in the workbench.

    Behavior:
    - No `CLAUDE.md` → write the full template (marker block only).
    - `CLAUDE.md` with marker → replace the marker block; user content
      outside the block is preserved.
    - `CLAUDE.md` without marker → refuse unless `force=True`, in which case
      back up the existing file to `CLAUDE.md.bak` and write the template.

    Raises:
        ConductorInstructionsConflict: when the workbench CLAUDE.md exists
        without a marker and `force` is False.
    """
    dir.mkdir(parents=True, exist_ok=True)
    claude_md = dir / "CLAUDE.md"

    if not claude_md.exists():
        claude_md.write_text(CONDUCTOR_INSTRUCTIONS)
        return UpdateResult(action="created", target=claude_md)

    content = claude_md.read_text()
    span = _find_marker_block(content)
    if span is not None:
        start, end = span
        claude_md.write_text(content[:start] + CONDUCTOR_INSTRUCTIONS + content[end:])
        return UpdateResult(action="replaced", target=claude_md)

    if not force:
        raise ConductorInstructionsConflict(
            f"{claude_md} exists but has no <conductor-instructions> marker. "
            f"Cannot distinguish plugin-managed content from your customizations. "
            f"Retry with --force to back up the existing file to CLAUDE.md.bak "
            f"and overwrite with the plugin template."
        )

    backup = claude_md.with_suffix(claude_md.suffix + ".bak")
    if backup.exists():
        backup.unlink()
    claude_md.rename(backup)
    claude_md.write_text(CONDUCTOR_INSTRUCTIONS)
    return UpdateResult(action="forced", target=claude_md, backup=backup)


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
