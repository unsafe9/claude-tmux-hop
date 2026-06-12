"""Self-test functionality for claude-tmux-hop."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestResult:
    """Result of a single test."""

    name: str
    passed: bool
    message: str | None = None


def test_state_transitions() -> list[TestResult]:
    """Test state transition logic (unit test, no tmux required)."""
    from .priority import STATE_PRIORITY, VALID_STATES

    results = []

    # Test valid states
    for state in ["waiting", "idle", "active"]:
        passed = state in VALID_STATES and state in STATE_PRIORITY
        results.append(
            TestResult(
                f"state_{state}_valid",
                passed,
                f"State '{state}' is valid" if passed else f"State '{state}' not found",
            )
        )

    # Test priority ordering (waiting < idle < active)
    priorities = [STATE_PRIORITY[s] for s in ["waiting", "idle", "active"]]
    passed = priorities == sorted(priorities)
    results.append(
        TestResult(
            "priority_ordering",
            passed,
            "waiting < idle < active" if passed else f"Unexpected order: {priorities}",
        )
    )

    # Test that waiting has lowest priority value (0)
    passed = STATE_PRIORITY["waiting"] == 0
    results.append(
        TestResult(
            "waiting_is_highest_priority",
            passed,
            "waiting has priority 0" if passed else f"waiting has priority {STATE_PRIORITY['waiting']}",
        )
    )

    return results


def test_priority_sorting() -> list[TestResult]:
    """Test priority sorting logic."""
    from .priority import get_cycle_group, sort_all_panes
    from .tmux import PaneInfo

    results = []

    # Create mock panes
    panes = [
        PaneInfo("%1", "active", 100, "/proj1", "main", 0),
        PaneInfo("%2", "waiting", 200, "/proj2", "main", 0),
        PaneInfo("%3", "idle", 150, "/proj3", "main", 0),
        PaneInfo("%4", "waiting", 100, "/proj4", "main", 0),  # Older waiting
    ]

    # Test sort_all_panes
    sorted_panes = sort_all_panes(panes)
    expected_order = ["%2", "%4", "%3", "%1"]  # waiting newest, waiting older, idle, active
    actual_order = [p.id for p in sorted_panes]

    results.append(
        TestResult(
            "sort_all_panes",
            actual_order == expected_order,
            f"Expected {expected_order}, got {actual_order}",
        )
    )

    # Test get_cycle_group priority mode
    group = get_cycle_group(panes, mode="priority")
    expected_ids = ["%2", "%4"]  # Only waiting panes, newest first
    actual_ids = [p.id for p in group]

    results.append(
        TestResult(
            "cycle_group_priority",
            actual_ids == expected_ids,
            f"Expected {expected_ids}, got {actual_ids}",
        )
    )

    # Test get_cycle_group flat mode
    group = get_cycle_group(panes, mode="flat")
    expected_count = 4

    results.append(
        TestResult(
            "cycle_group_flat",
            len(group) == expected_count,
            f"Expected {expected_count} panes, got {len(group)}",
        )
    )

    # Test empty panes
    empty_group = get_cycle_group([], mode="priority")
    results.append(
        TestResult(
            "cycle_group_empty",
            len(empty_group) == 0,
            "Empty input returns empty list",
        )
    )

    return results


def validate_hooks_json() -> list[TestResult]:
    """Validate hooks.json structure."""
    results = []

    # Find hooks.json
    import claude_tmux_hop

    package_path = Path(claude_tmux_hop.__file__).parent

    # Try multiple locations: source layout, pip package data, cwd
    possible_paths = [
        package_path.parent.parent / "hooks" / "hooks.json",  # source: src/../hooks/
        package_path / "hooks" / "hooks.json",  # pip: bundled as package data
        package_path.parent / "hooks" / "hooks.json",  # alternate pip layout
        Path.cwd() / "hooks" / "hooks.json",  # current directory fallback
    ]

    hooks_path = None
    for p in possible_paths:
        if p.exists():
            hooks_path = p
            break

    if not hooks_path:
        results.append(TestResult("hooks_file_exists", False, "hooks.json not found"))
        return results

    results.append(TestResult("hooks_file_exists", True, str(hooks_path)))

    # Parse JSON
    try:
        data = json.loads(hooks_path.read_text())
    except json.JSONDecodeError as e:
        results.append(TestResult("hooks_valid_json", False, str(e)))
        return results

    results.append(TestResult("hooks_valid_json", True, "Valid JSON"))

    # Validate structure
    hooks = data.get("hooks", {})
    required_events = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"]
    for event in required_events:
        passed = event in hooks
        results.append(
            TestResult(
                f"hooks_has_{event}",
                passed,
                f"Event {event} present" if passed else f"Missing event: {event}",
            )
        )

    # Check that all hooks use the correct command pattern
    command_pattern = "claude-tmux-hop"
    all_commands_valid = True
    for event, hook_list in hooks.items():
        for hook_entry in hook_list:
            for hook in hook_entry.get("hooks", []):
                if hook.get("type") == "command":
                    cmd = hook.get("command", "")
                    if command_pattern not in cmd:
                        all_commands_valid = False

    results.append(
        TestResult(
            "hooks_commands_valid",
            all_commands_valid,
            "All hooks use claude-tmux-hop command" if all_commands_valid else "Some hooks have invalid commands",
        )
    )

    return results


def test_dialog_detection() -> list[TestResult]:
    """Test dialog detection logic (unit test, no tmux required)."""
    from .tmux import has_active_dialog

    results = []

    # Prompt ❯ above separator → dismissed
    content = "Some output\n───\n❯ \n───\n  Ctx: 24%"
    results.append(
        TestResult(
            "dialog_prompt_above_separator",
            has_active_dialog(content) is False,
            "Prompt ❯ above status separator means dismissed",
        )
    )

    # Prompt ❯ with user text above separator → dismissed
    content = "Some output\n───\n❯ hello world\n───\n  Ctx: 24%"
    results.append(
        TestResult(
            "dialog_prompt_with_text",
            has_active_dialog(content) is False,
            "Prompt ❯ with text above separator means dismissed",
        )
    )

    # Dialog option (not ❯) above separator → active
    content = "? Pick one\n❯ Option A\n  Option B\n───\n  Ctx: 24%"
    results.append(
        TestResult(
            "dialog_option_above_separator",
            has_active_dialog(content) is True,
            "Non-prompt line above separator means dialog active",
        )
    )

    # Empty content → conservative (active)
    results.append(
        TestResult(
            "dialog_empty_content",
            has_active_dialog("") is True,
            "Empty content is conservative (assume active)",
        )
    )

    # Whitespace-only content → conservative (active)
    results.append(
        TestResult(
            "dialog_whitespace_only",
            has_active_dialog("   \n  \n  ") is True,
            "Whitespace-only is conservative (assume active)",
        )
    )

    # No separator at all → conservative (active)
    content = "Some text\n❯ Option\n  Another"
    results.append(
        TestResult(
            "dialog_no_separator",
            has_active_dialog(content) is True,
            "No separator means conservative (assume active)",
        )
    )

    # No ❯ anywhere, no separator → conservative (active)
    content = "Some output\nMore output"
    results.append(
        TestResult(
            "dialog_no_prompt_no_separator",
            has_active_dialog(content) is True,
            "No prompt or separator is conservative (assume active)",
        )
    )

    return results


def test_terminal_detection() -> list[TestResult]:
    """Test terminal app detection helpers (unit test, no tmux required)."""
    from . import notify

    results = []

    original_get_global_option = notify.get_global_option
    original_environ = dict(notify.os.environ)

    try:
        notify.get_global_option = lambda _name, default="": default
        notify.os.environ.clear()
        notify.os.environ.update({"TERM_PROGRAM": "ghostty"})

        app = notify._get_terminal_app()
        results.append(
            TestResult(
                "terminal_detection_ghostty_lowercase",
                app == "Ghostty",
                f"Expected Ghostty, got {app}",
            )
        )

        notify.os.environ.update(
            {
                "__CFBundleIdentifier": "com.apple.Terminal",
                "TERM_PROGRAM": "ghostty",
            }
        )

        app = notify._get_terminal_app()
        results.append(
            TestResult(
                "terminal_detection_ghostty_over_stale_terminal_bundle",
                app == "Ghostty",
                f"Expected Ghostty, got {app}",
            )
        )
    finally:
        notify.get_global_option = original_get_global_option
        notify.os.environ.clear()
        notify.os.environ.update(original_environ)

    return results


def test_macos_focus_behaviors() -> list[TestResult]:
    """Test macOS focus scripts (unit test, no osascript required)."""
    from .notify import macos

    results = []
    scripts: list[str] = []
    original_run_osascript = macos._run_osascript
    original_run_osascript_output = macos._run_osascript_output

    def fake_run_osascript(script: str) -> bool:
        scripts.append(script)
        return True

    def fake_run_osascript_output(script: str) -> str:
        scripts.append(script)
        return "true"

    try:
        macos._run_osascript = fake_run_osascript
        macos._run_osascript_output = fake_run_osascript_output
        focused = macos.MacOSFocusHandler().focus("Ghostty", "claude-session")
        script = scripts[-1] if scripts else ""

        results.append(
            TestResult(
                "macos_focus_ghostty_uses_process_focus",
                focused and 'application process "ghostty"' in script,
                "Ghostty focus uses System Events process focus",
            )
        )
        results.append(
            TestResult(
                "macos_focus_ghostty_does_not_activate_app",
                'tell application "Ghostty" to activate' not in script,
                "Ghostty focus avoids app activate",
            )
        )

        scripts.clear()

        def fake_failed_osascript_output(script: str) -> str:
            scripts.append(script)
            return "false"

        macos._run_osascript_output = fake_failed_osascript_output
        focused = macos.MacOSFocusHandler().focus("Ghostty", "claude-session")
        avoided_activate = all(
            'tell application "Ghostty" to activate' not in s for s in scripts
        )

        results.append(
            TestResult(
                "macos_focus_ghostty_failure_does_not_launch_app",
                not focused and avoided_activate,
                "Ghostty focus failure does not fall back to app activate",
            )
        )
    finally:
        macos._run_osascript = original_run_osascript
        macos._run_osascript_output = original_run_osascript_output

    return results


def test_macos_terminal_app_from_process_tree() -> list[TestResult]:
    """Test macOS terminal app detection from process executable paths."""
    from .notify import macos

    results = []

    cases = [
        (
            "/Applications/Ghostty.app/Contents/MacOS/ghostty",
            "Ghostty",
            "extracts Ghostty from app bundle path",
        ),
        (
            "/Applications/iTerm.app/Contents/MacOS/iTerm2",
            "iTerm",
            "extracts iTerm from app bundle path",
        ),
        (
            "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal",
            "Terminal",
            "extracts Terminal from app bundle path",
        ),
        ("/usr/bin/login", None, "non-terminal binary returns None"),
        ("", None, "empty input returns None"),
        ("ghostty", None, "bare basename without bundle returns None"),
    ]

    for path, expected, message in cases:
        actual = macos._app_from_executable_path(path)
        results.append(
            TestResult(
                f"macos_app_from_path__{expected or 'none'}__{path[:20]}",
                actual == expected,
                f"{message}: got {actual!r}, expected {expected!r}",
            )
        )

    # Walk a synthetic process tree: tmux client -> shell -> login -> Ghostty
    procs = {
        "8947": ("53850", "tmux"),
        "53850": ("53849", "-/bin/zsh"),
        "53849": ("75257", "/usr/bin/login"),
        "75257": ("1", "/Applications/Ghostty.app/Contents/MacOS/ghostty"),
    }
    walked = macos._walk_pid_to_terminal_app("8947", procs=procs)
    results.append(
        TestResult(
            "macos_walk_finds_ghostty_ancestor",
            walked == "Ghostty",
            f"Expected Ghostty by walking ancestors, got {walked!r}",
        )
    )

    # Walk terminates cleanly when no terminal app is found in ancestry
    orphan_procs = {
        "100": ("1", "tmux"),
    }
    walked = macos._walk_pid_to_terminal_app("100", procs=orphan_procs)
    results.append(
        TestResult(
            "macos_walk_returns_none_without_terminal",
            walked is None,
            f"Expected None when no terminal ancestor, got {walked!r}",
        )
    )

    # Walk handles a missing start pid gracefully
    walked = macos._walk_pid_to_terminal_app("99999", procs=procs)
    results.append(
        TestResult(
            "macos_walk_missing_pid_returns_none",
            walked is None,
            f"Expected None for missing pid, got {walked!r}",
        )
    )

    # Regression: when the requested session has no attached client, fall back
    # to the most-recently-active client across all sessions. Without this
    # fallback, hooks fired in background tmux sessions return None and
    # `_get_terminal_app` rolls down to the stale `__CFBundleIdentifier`,
    # which causes a phantom `tell application "Terminal" activate` and
    # launches Terminal.app for users on other terminal emulators.
    original_list = macos._list_tmux_clients
    original_walk = macos._walk_pid_to_terminal_app

    def fake_list(session_name):
        return [] if session_name else ["123\t8947"]

    macos._list_tmux_clients = fake_list
    macos._walk_pid_to_terminal_app = lambda pid, procs=None: "Ghostty" if pid == "8947" else None
    try:
        detected = macos.detect_terminal_app_via_tmux_client("palm")
    finally:
        macos._list_tmux_clients = original_list
        macos._walk_pid_to_terminal_app = original_walk

    results.append(
        TestResult(
            "macos_detect_falls_back_to_unscoped_clients",
            detected == "Ghostty",
            f"Expected unscoped fallback to surface Ghostty, got {detected!r}",
        )
    )

    return results


def test_terminal_detection_prefers_tmux_client() -> list[TestResult]:
    """`_get_terminal_app` must trust tmux client ancestry over stale env vars."""
    from . import notify

    results = []
    original_get_global_option = notify.get_global_option
    original_environ = dict(notify.os.environ)
    original_detect = notify.detect_terminal_app_via_tmux_client

    try:
        notify.get_global_option = lambda _name, default="": default
        notify.os.environ.clear()
        # Simulate the real-world bug: tmux server inherited Terminal.app env,
        # user is now attached from Ghostty.
        notify.os.environ.update(
            {
                "TMUX": "/tmp/tmux/default,1,0",
                "__CFBundleIdentifier": "com.apple.Terminal",
                "TERM_PROGRAM": "tmux",
            }
        )
        notify.detect_terminal_app_via_tmux_client = (
            lambda session_name=None: "Ghostty"
        )
        # Force macOS platform branch
        original_platform = notify.sys.platform
        try:
            notify.sys.platform = "darwin"
            app = notify._get_terminal_app("some-session")
        finally:
            notify.sys.platform = original_platform

        results.append(
            TestResult(
                "terminal_detection_prefers_tmux_client_over_stale_bundle",
                app == "Ghostty",
                f"Expected Ghostty from tmux client walk, got {app!r}",
            )
        )

        # Fall through to env-var logic when tmux client detection returns None.
        notify.detect_terminal_app_via_tmux_client = lambda session_name=None: None
        try:
            notify.sys.platform = "darwin"
            app = notify._get_terminal_app("some-session")
        finally:
            notify.sys.platform = original_platform

        results.append(
            TestResult(
                "terminal_detection_falls_back_when_tmux_client_unknown",
                app == "Terminal",
                f"Expected Terminal env-var fallback, got {app!r}",
            )
        )
    finally:
        notify.get_global_option = original_get_global_option
        notify.detect_terminal_app_via_tmux_client = original_detect
        notify.os.environ.clear()
        notify.os.environ.update(original_environ)

    return results


def test_pane_context_resolution() -> list[TestResult]:
    """Test hook pane context resolution (unit test, no tmux required)."""
    from . import cli

    results = []
    original_environ = dict(cli.os.environ)
    original_get_current_session_window = cli.get_current_session_window

    def fake_get_current_session_window(pane_id: str | None = None) -> tuple[str, int]:
        if pane_id == "%target":
            return "target-session", 7
        return "active-session", 3

    try:
        cli.os.environ.clear()
        cli.os.environ.update({"TMUX_PANE": "%target"})
        cli.get_current_session_window = fake_get_current_session_window

        context = cli._build_pane_context("project")
        results.append(
            TestResult(
                "pane_context_uses_tmux_pane_target",
                context is not None
                and context.pane_id == "%target"
                and context.session == "target-session"
                and context.window == 7,
                f"Expected %target in target-session:7, got {context}",
            )
        )
    finally:
        cli.get_current_session_window = original_get_current_session_window
        cli.os.environ.clear()
        cli.os.environ.update(original_environ)

    return results


def test_normalize_task() -> list[TestResult]:
    """Test task normalization helper (unit test, no tmux required)."""
    from .cli import MAX_TASK_STORED, _normalize_task

    results = []

    cases = [
        ("", "", "empty input returns empty"),
        ("   ", "", "whitespace-only returns empty"),
        ("hello world", "hello world", "simple text passes through"),
        ("line one\nline two", "line one", "multiline returns first non-empty line"),
        ("\n\nfirst real line\nignored", "first real line", "skips leading blank lines"),
        ("```python\ncode\n```", "code", "code-fence prefix stripped (next line is the content)"),
        ("```\nbare", "bare", "bare code fence on first line falls through to next"),
        ("> quoted text", "quoted text", "blockquote prefix stripped"),
        ("a\tb  c", "a b c", "tabs and multi-spaces collapsed"),
    ]

    for raw, expected, desc in cases:
        actual = _normalize_task(raw)
        results.append(
            TestResult(
                f"normalize_task__{desc[:32]}",
                actual == expected,
                f"{desc}: got {actual!r}, expected {expected!r}",
            )
        )

    long_input = "x" * (MAX_TASK_STORED + 50)
    truncated = _normalize_task(long_input)
    results.append(
        TestResult(
            "normalize_task__truncates_to_max_stored",
            len(truncated) == MAX_TASK_STORED and truncated.endswith("…"),
            f"Expected length {MAX_TASK_STORED} with ellipsis, got len={len(truncated)} suffix={truncated[-2:]!r}",
        )
    )

    return results


def test_extract_task_from_transcript() -> list[TestResult]:
    """Test transcript jsonl parsing for task extraction."""
    from .cli import _extract_task_from_transcript

    results = []

    def write_jsonl(lines: list[dict | str]) -> Path:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        for line in lines:
            if isinstance(line, dict):
                f.write(json.dumps(line) + "\n")
            else:
                f.write(line + "\n")
        f.close()
        return Path(f.name)

    # Case 1: ai-title present → use it
    p = write_jsonl([
        {"type": "user", "message": {"content": "hi"}},
        {"type": "ai-title", "aiTitle": "Refactor auth module"},
        {"type": "last-prompt", "lastPrompt": "hi"},
    ])
    try:
        actual = _extract_task_from_transcript(str(p))
        results.append(TestResult(
            "extract_task__ai_title_present",
            actual == "Refactor auth module",
            f"Expected 'Refactor auth module', got {actual!r}",
        ))
    finally:
        p.unlink(missing_ok=True)

    # Case 2: only last-prompt → fallback
    p = write_jsonl([
        {"type": "user", "message": {"content": "hi"}},
        {"type": "last-prompt", "lastPrompt": "Investigate cache stampede"},
    ])
    try:
        actual = _extract_task_from_transcript(str(p))
        results.append(TestResult(
            "extract_task__fallback_to_last_prompt",
            actual == "Investigate cache stampede",
            f"Expected 'Investigate cache stampede', got {actual!r}",
        ))
    finally:
        p.unlink(missing_ok=True)

    # Case 3: neither → empty
    p = write_jsonl([
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"content": "ok"}},
    ])
    try:
        actual = _extract_task_from_transcript(str(p))
        results.append(TestResult(
            "extract_task__no_signal_returns_empty",
            actual == "",
            f"Expected '', got {actual!r}",
        ))
    finally:
        p.unlink(missing_ok=True)

    # Case 4: ai-title appears twice → most recent wins
    p = write_jsonl([
        {"type": "ai-title", "aiTitle": "Old topic"},
        {"type": "user", "message": {"content": "..."}},
        {"type": "ai-title", "aiTitle": "New topic"},
    ])
    try:
        actual = _extract_task_from_transcript(str(p))
        results.append(TestResult(
            "extract_task__most_recent_ai_title_wins",
            actual == "New topic",
            f"Expected 'New topic', got {actual!r}",
        ))
    finally:
        p.unlink(missing_ok=True)

    # Case 5: corrupt JSON lines are tolerated
    p = write_jsonl([
        "not-json garbage",
        {"type": "ai-title", "aiTitle": "Survives garbage"},
        "{half-json",
    ])
    try:
        actual = _extract_task_from_transcript(str(p))
        results.append(TestResult(
            "extract_task__tolerates_corrupt_json",
            actual == "Survives garbage",
            f"Expected 'Survives garbage', got {actual!r}",
        ))
    finally:
        p.unlink(missing_ok=True)

    # Case 6: missing file → empty, no exception
    actual = _extract_task_from_transcript("/nonexistent/path.jsonl")
    results.append(TestResult(
        "extract_task__missing_file_returns_empty",
        actual == "",
        f"Expected '', got {actual!r}",
    ))

    return results


def test_cmd_list_json() -> list[TestResult]:
    """`list --json` emits a structured row per pane with git context."""
    from . import cli
    from .tmux import PaneInfo

    results = []

    fake_panes = [
        PaneInfo("%1", "waiting", 1700000000, "/repo/proj", "main", 0, task="hello"),
        PaneInfo("%2", "idle", 1700000010, "/no-git", "main", 1),
    ]

    git_fixture = {
        "/repo/proj": {"branch": "feature/foo", "worktree_root": "/repo/proj"},
        "/no-git": {},
    }

    original_get_hop_panes = cli.get_hop_panes
    original_validate = cli.validate_waiting_panes
    original_get_git_context = cli.get_git_context
    original_is_in_tmux = cli.is_in_tmux

    try:
        cli.get_hop_panes = lambda validate=True: list(fake_panes)
        cli.validate_waiting_panes = lambda panes: None
        cli.get_git_context = lambda cwd: dict(git_fixture.get(cwd, {}))
        cli.is_in_tmux = lambda: True

        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = cli.cmd_list(argparse.Namespace(json=True))

        parsed = json.loads(buf.getvalue())
    finally:
        cli.get_hop_panes = original_get_hop_panes
        cli.validate_waiting_panes = original_validate
        cli.get_git_context = original_get_git_context
        cli.is_in_tmux = original_is_in_tmux

    results.append(TestResult(
        "cmd_list_json__exit_zero",
        rc == 0,
        f"Expected return 0, got {rc}",
    ))
    results.append(TestResult(
        "cmd_list_json__row_count",
        isinstance(parsed, list) and len(parsed) == 2,
        f"Expected 2 rows, got {parsed!r}",
    ))

    by_id = {row["id"]: row for row in parsed} if isinstance(parsed, list) else {}
    expected_keys = {"id", "state", "timestamp", "cwd", "session", "window",
                     "project", "task"}
    row1 = by_id.get("%1", {})
    row2 = by_id.get("%2", {})

    results.append(TestResult(
        "cmd_list_json__core_keys_present",
        expected_keys.issubset(row1.keys()),
        f"Missing core keys on %1: {expected_keys - set(row1.keys())}",
    ))
    results.append(TestResult(
        "cmd_list_json__git_context_present",
        row1.get("branch") == "feature/foo"
        and row1.get("worktree_root") == "/repo/proj",
        f"Expected git context for %1, got {row1!r}",
    ))
    results.append(TestResult(
        "cmd_list_json__git_context_omitted_when_absent",
        "branch" not in row2 and "worktree_root" not in row2,
        f"Expected no git keys for %2, got {row2!r}",
    ))
    results.append(TestResult(
        "cmd_list_json__task_passthrough",
        row1.get("task") == "hello" and row2.get("task") == "",
        f"Task fields mismatch: %1={row1.get('task')!r}, %2={row2.get('task')!r}",
    ))

    return results


def _build_full_parser():
    """Build the full CLI parser wired with no-op handlers (for arg-parsing tests)."""
    from .parser import create_parser

    def noop(_args):  # pragma: no cover - never called in arg-parsing tests
        return 0

    return create_parser(
        cmd_register=noop,
        cmd_clear=noop,
        cmd_cycle=noop,
        cmd_back=noop,
        cmd_picker_data=noop,
        cmd_switch=noop,
        cmd_list=noop,
        cmd_discover=noop,
        cmd_prune=noop,
        cmd_status=noop,
        cmd_status_inbox=noop,
        cmd_inbox=noop,
        cmd_inbox_clear=noop,
        cmd_install=noop,
        cmd_update=noop,
        cmd_spawn_task=noop,
        cmd_send_prompt=noop,
        cmd_conductor=noop,
        cmd_conductor_context=noop,
        cmd_conductor_prompt_context=noop,
    )


def test_spawn_task_arg_parsing() -> list[TestResult]:
    """`spawn-task` parses required + optional flags into the expected namespace."""
    results = []

    parser = _build_full_parser()

    ns = parser.parse_args([
        "spawn-task",
        "--cwd", "/tmp",
        "--prompt", "hi",
        "--session", "main",
    ])
    results.append(TestResult(
        "spawn_task_arg_parsing__required_fields",
        ns.cwd == "/tmp" and ns.prompt == "hi" and ns.session == "main",
        f"Unexpected namespace: {ns!r}",
    ))
    results.append(TestResult(
        "spawn_task_arg_parsing__switch_default_true",
        ns.switch is True,
        f"Expected switch=True default, got {ns.switch!r}",
    ))
    results.append(TestResult(
        "spawn_task_arg_parsing__window_name_optional",
        ns.window_name is None,
        f"Expected window_name=None default, got {ns.window_name!r}",
    ))

    ns2 = parser.parse_args([
        "spawn-task",
        "--cwd", "/tmp",
        "--prompt", "hi",
        "--session", "main",
        "--window-name", "feature-x",
        "--no-switch",
    ])
    results.append(TestResult(
        "spawn_task_arg_parsing__no_switch_window_name",
        ns2.switch is False and ns2.window_name == "feature-x",
        f"Expected no-switch + window_name=feature-x, got {ns2!r}",
    ))

    return results


def test_send_prompt_arg_parsing() -> list[TestResult]:
    """`send-prompt` parses required + optional flags into the expected namespace."""
    results = []

    parser = _build_full_parser()

    ns = parser.parse_args([
        "send-prompt",
        "--pane", "%5",
        "--prompt", "follow-up",
    ])
    results.append(TestResult(
        "send_prompt_arg_parsing__required_fields",
        ns.pane == "%5" and ns.prompt == "follow-up",
        f"Unexpected namespace: {ns!r}",
    ))
    results.append(TestResult(
        "send_prompt_arg_parsing__switch_default_true",
        ns.switch is True,
        f"Expected switch=True default, got {ns.switch!r}",
    ))
    results.append(TestResult(
        "send_prompt_arg_parsing__force_default_false",
        ns.force is False,
        f"Expected force=False default, got {ns.force!r}",
    ))

    ns2 = parser.parse_args([
        "send-prompt",
        "--pane", "%9",
        "--prompt", "x",
        "--force",
        "--no-switch",
    ])
    results.append(TestResult(
        "send_prompt_arg_parsing__force_no_switch",
        ns2.force is True and ns2.switch is False,
        f"Expected force=True switch=False, got {ns2!r}",
    ))

    return results


def test_conductor_arg_parsing() -> list[TestResult]:
    """`conductor` parses mode + respawn + force flags into the expected namespace."""
    results = []

    parser = _build_full_parser()

    ns_default = parser.parse_args(["conductor"])
    results.append(TestResult(
        "conductor_arg_parsing__defaults_to_popup_mode",
        ns_default.mode == "popup" and ns_default.respawn is False and ns_default.force is False,
        f"Expected mode=popup, respawn=False, force=False; got {ns_default!r}",
    ))

    ns_respawn = parser.parse_args(["conductor", "--popup", "--respawn"])
    results.append(TestResult(
        "conductor_arg_parsing__respawn_flag",
        ns_respawn.mode == "popup" and ns_respawn.respawn is True,
        f"Expected mode=popup, respawn=True; got {ns_respawn!r}",
    ))

    ns_update = parser.parse_args(["conductor", "--update-instructions"])
    results.append(TestResult(
        "conductor_arg_parsing__update_instructions",
        ns_update.mode == "update_instructions" and ns_update.force is False,
        f"Expected mode=update_instructions, force=False; got {ns_update!r}",
    ))

    ns_force = parser.parse_args(["conductor", "--update-instructions", "--force"])
    results.append(TestResult(
        "conductor_arg_parsing__force_flag",
        ns_force.mode == "update_instructions" and ns_force.force is True,
        f"Expected mode=update_instructions, force=True; got {ns_force!r}",
    ))

    ns_kill = parser.parse_args(["conductor", "--kill"])
    results.append(TestResult(
        "conductor_arg_parsing__kill_mode",
        ns_kill.mode == "kill",
        f"Expected mode=kill; got {ns_kill!r}",
    ))

    # --popup, --update-instructions, --kill are mutually exclusive
    mutex_ok = False
    try:
        with redirect_stderr(io.StringIO()):
            parser.parse_args(["conductor", "--popup", "--update-instructions"])
    except SystemExit:
        mutex_ok = True
    results.append(TestResult(
        "conductor_arg_parsing__popup_and_update_are_mutex",
        mutex_ok,
        "Expected SystemExit when combining --popup and --update-instructions",
    ))

    kill_mutex_ok = False
    try:
        with redirect_stderr(io.StringIO()):
            parser.parse_args(["conductor", "--kill", "--update-instructions"])
    except SystemExit:
        kill_mutex_ok = True
    results.append(TestResult(
        "conductor_arg_parsing__kill_and_update_are_mutex",
        kill_mutex_ok,
        "Expected SystemExit when combining --kill and --update-instructions",
    ))

    return results


def test_update_instructions() -> list[TestResult]:
    """`update_conductor_instructions` handles all four file-state cases."""
    import tempfile

    from .install import (
        CONDUCTOR_INSTRUCTIONS,
        CONDUCTOR_MARKER_CLOSE,
        CONDUCTOR_MARKER_OPEN,
        ConductorInstructionsConflict,
        update_conductor_instructions,
    )

    results = []

    # Case 1: no file yet → created
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        result = update_conductor_instructions(tmp_path)
        claude_md = tmp_path / "CLAUDE.md"
        results.append(TestResult(
            "update_instructions__no_file_creates",
            result.action == "created"
            and result.backup is None
            and claude_md.exists()
            and CONDUCTOR_MARKER_OPEN in claude_md.read_text()
            and CONDUCTOR_MARKER_CLOSE in claude_md.read_text(),
            f"Expected created action with marker file; got {result!r}",
        ))

    # Case 2: file with marker + outside content → replaced; outside preserved
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        claude_md = tmp_path / "CLAUDE.md"
        header = "# My header\nuser content above\n\n"
        footer = "\n## My notes\nuser content below\n"
        old_body = (
            f"{CONDUCTOR_MARKER_OPEN}\n"
            f"OLD PLUGIN BODY — should disappear after update\n"
            f"{CONDUCTOR_MARKER_CLOSE}"
        )
        claude_md.write_text(header + old_body + footer)
        result = update_conductor_instructions(tmp_path)
        new_content = claude_md.read_text()
        results.append(TestResult(
            "update_instructions__marker_preserves_outside",
            result.action == "replaced"
            and result.backup is None
            and new_content.startswith(header)
            and new_content.endswith(footer)
            and "OLD PLUGIN BODY" not in new_content
            and CONDUCTOR_INSTRUCTIONS in new_content,
            f"Outside content not preserved or new body missing; got:\n{new_content!r}",
        ))

    # Case 3: file without marker + force=False → ConductorInstructionsConflict
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        claude_md = tmp_path / "CLAUDE.md"
        original = "totally custom claude.md\n"
        claude_md.write_text(original)
        conflict_raised = False
        try:
            update_conductor_instructions(tmp_path, force=False)
        except ConductorInstructionsConflict:
            conflict_raised = True
        results.append(TestResult(
            "update_instructions__no_marker_blocks_without_force",
            conflict_raised and claude_md.read_text() == original,
            f"Expected conflict + untouched file; got raised={conflict_raised}, content={claude_md.read_text()!r}",
        ))

    # Case 4: file without marker + force=True → forced; backup contains original
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        claude_md = tmp_path / "CLAUDE.md"
        original = "totally custom claude.md\n"
        claude_md.write_text(original)
        result = update_conductor_instructions(tmp_path, force=True)
        backup = tmp_path / "CLAUDE.md.bak"
        results.append(TestResult(
            "update_instructions__force_writes_backup",
            result.action == "forced"
            and result.backup == backup
            and backup.exists()
            and backup.read_text() == original
            and CONDUCTOR_INSTRUCTIONS in claude_md.read_text(),
            f"Expected forced action with backup; got result={result!r}, "
            f"backup_exists={backup.exists()}, claude_md_has_template={'plugin' in claude_md.read_text()}",
        ))

    return results


def test_conductor_context() -> list[TestResult]:
    """`cmd_conductor_context` emits SessionStart JSON only inside the workbench
    and only when the marker is missing."""
    import argparse
    import json as _json
    import tempfile

    from . import cli
    from .install import CONDUCTOR_INSTRUCTIONS, CONDUCTOR_MARKER_OPEN

    results = []

    original_resolve = cli.resolve_conductor_dir
    original_cwd = Path.cwd

    def _patch(workbench: Path, cwd: Path) -> None:
        cli.resolve_conductor_dir = lambda: workbench  # type: ignore[assignment]
        Path.cwd = staticmethod(lambda: cwd)  # type: ignore[assignment]

    def _restore() -> None:
        cli.resolve_conductor_dir = original_resolve  # type: ignore[assignment]
        Path.cwd = original_cwd  # type: ignore[assignment]

    try:
        # Case 1: cwd outside workbench → no output, return 0
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "workbench"
            workbench.mkdir()
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            _patch(workbench, elsewhere)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_context(argparse.Namespace())
            results.append(TestResult(
                "conductor_context__outside_workbench_silent",
                rc == 0 and buf.getvalue() == "",
                f"Expected rc=0 and empty stdout; got rc={rc}, out={buf.getvalue()!r}",
            ))

        # Case 2: cwd == workbench, no CLAUDE.md → JSON with additionalContext + systemMessage
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp)
            _patch(workbench, workbench)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_context(argparse.Namespace())
            payload = _json.loads(buf.getvalue()) if buf.getvalue() else {}
            hso = payload.get("hookSpecificOutput", {})
            results.append(TestResult(
                "conductor_context__no_claude_md_injects",
                rc == 0
                and hso.get("hookEventName") == "SessionStart"
                and hso.get("additionalContext") == CONDUCTOR_INSTRUCTIONS
                and "--update-instructions" in (hso.get("systemMessage") or ""),
                f"Expected hookSpecificOutput with both fields; got {payload!r}",
            ))

        # Case 3: cwd == workbench, CLAUDE.md with marker → no output
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp)
            (workbench / "CLAUDE.md").write_text(
                f"{CONDUCTOR_MARKER_OPEN}\nsome body\n</conductor-instructions>\n"
            )
            _patch(workbench, workbench)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_context(argparse.Namespace())
            results.append(TestResult(
                "conductor_context__marker_present_silent",
                rc == 0 and buf.getvalue() == "",
                f"Expected rc=0 and empty stdout; got rc={rc}, out={buf.getvalue()!r}",
            ))

        # Case 4: cwd == workbench, CLAUDE.md without marker → JSON injection
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp)
            (workbench / "CLAUDE.md").write_text("totally custom claude.md\n")
            _patch(workbench, workbench)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_context(argparse.Namespace())
            payload = _json.loads(buf.getvalue()) if buf.getvalue() else {}
            hso = payload.get("hookSpecificOutput", {})
            results.append(TestResult(
                "conductor_context__no_marker_injects",
                rc == 0
                and hso.get("additionalContext") == CONDUCTOR_INSTRUCTIONS
                and "--update-instructions" in (hso.get("systemMessage") or ""),
                f"Expected injection JSON; got {payload!r}",
            ))
    finally:
        _restore()

    return results


def test_conductor_prompt_context() -> list[TestResult]:
    """`cmd_conductor_prompt_context` emits a UserPromptSubmit snapshot only
    inside the workbench, and survives `_build_pane_records` exceptions silently."""
    import argparse
    import json as _json
    import tempfile

    from . import cli

    results = []

    original_resolve = cli.resolve_conductor_dir
    original_cwd = Path.cwd
    original_build = cli._build_pane_records

    sample_record = {
        "id": "%1",
        "state": "idle",
        "timestamp": 0,
        "cwd": "/x",
        "session": "s",
        "window": 1,
        "project": "x",
        "branch": "main",
        "worktree_root": "/x",
        "task": "demo",
    }

    def _patch(workbench: Path, cwd: Path, build_impl=None) -> None:
        cli.resolve_conductor_dir = lambda: workbench  # type: ignore[assignment]
        Path.cwd = staticmethod(lambda: cwd)  # type: ignore[assignment]
        cli._build_pane_records = build_impl or (lambda: [sample_record])  # type: ignore[assignment]

    def _restore() -> None:
        cli.resolve_conductor_dir = original_resolve  # type: ignore[assignment]
        Path.cwd = original_cwd  # type: ignore[assignment]
        cli._build_pane_records = original_build  # type: ignore[assignment]

    try:
        # Case 1: cwd outside workbench → no output, return 0
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "workbench"
            workbench.mkdir()
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            _patch(workbench, elsewhere)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_prompt_context(argparse.Namespace())
            results.append(TestResult(
                "conductor_prompt_context__outside_workbench_silent",
                rc == 0 and buf.getvalue() == "",
                f"Expected rc=0 and empty stdout; got rc={rc}, out={buf.getvalue()!r}",
            ))

        # Case 2: cwd == workbench → JSON UserPromptSubmit payload with snapshot
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp)
            _patch(workbench, workbench)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_prompt_context(argparse.Namespace())
            payload = _json.loads(buf.getvalue()) if buf.getvalue() else {}
            hso = payload.get("hookSpecificOutput", {})
            ctx = hso.get("additionalContext") or ""
            # The additionalContext is "<intro>:\n<json>". Parse out the JSON tail.
            _intro, _sep, tail = ctx.partition(":\n")
            try:
                records = _json.loads(tail)
            except Exception:
                records = None
            results.append(TestResult(
                "conductor_prompt_context__inside_workbench_injects",
                rc == 0
                and hso.get("hookEventName") == "UserPromptSubmit"
                and isinstance(records, list)
                and records == [sample_record],
                f"Expected UserPromptSubmit injection with snapshot; got {payload!r}",
            ))

        # Case 3: _build_pane_records raises → silent rc=0 (don't crash the hook)
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp)

            def _boom():
                raise RuntimeError("tmux gone")

            _patch(workbench, workbench, build_impl=_boom)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_conductor_prompt_context(argparse.Namespace())
            results.append(TestResult(
                "conductor_prompt_context__builder_error_silent",
                rc == 0 and buf.getvalue() == "",
                f"Expected rc=0 and empty stdout on builder error; got rc={rc}, out={buf.getvalue()!r}",
            ))
    finally:
        _restore()

    return results


def test_send_prompt_blocks_active_pane() -> list[TestResult]:
    """`cmd_send_prompt` refuses active panes unless `--force` is set."""
    from . import cli

    results = []

    call_log: list[tuple[str, ...]] = []
    send_called: list[tuple] = []

    def fake_run_tmux(*args, check=True):
        call_log.append(args)
        # Stub @hop-state lookup → "active"; pane existence probe → echo id.
        if args[:2] == ("show-option", "-pqv"):
            return "active"
        if args[0] == "display-message":
            return args[-1] if args[-1].startswith("%") else "%9"
        return ""

    def fake_send_prompt_to_pane(pane_id, prompt, switch=True):
        send_called.append((pane_id, prompt, switch))

    original_run_tmux = cli.run_tmux
    original_send = cli.send_prompt_to_pane
    original_is_in_tmux = cli.is_in_tmux

    try:
        cli.run_tmux = fake_run_tmux
        cli.send_prompt_to_pane = fake_send_prompt_to_pane
        cli.is_in_tmux = lambda: True

        # Active without --force → refused
        with redirect_stderr(io.StringIO()):
            rc = cli.cmd_send_prompt(argparse.Namespace(
                pane="%9", prompt="x", switch=True, force=False,
            ))
        results.append(TestResult(
            "send_prompt_blocks_active__refused",
            rc == 1 and not send_called,
            f"Expected refusal rc=1 and no send, got rc={rc}, send_called={send_called}",
        ))

        # Active with --force → passes through
        with redirect_stderr(io.StringIO()):
            rc = cli.cmd_send_prompt(argparse.Namespace(
                pane="%9", prompt="x", switch=True, force=True,
            ))
        results.append(TestResult(
            "send_prompt_blocks_active__force_overrides",
            rc == 0 and send_called == [("%9", "x", True)],
            f"Expected rc=0 + send, got rc={rc}, send_called={send_called}",
        ))
    finally:
        cli.run_tmux = original_run_tmux
        cli.send_prompt_to_pane = original_send
        cli.is_in_tmux = original_is_in_tmux

    return results


def test_conductor_session_excluded() -> list[TestResult]:
    """Conductor-session panes are filtered out of `get_hop_panes()`."""
    from . import tmux

    results = []

    list_panes_output = "\n".join([
        # pane_id\tstate\tts\tcwd\tsession\twindow\ttask
        "%1\twaiting\t1700000000\t/repo/a\tmain\t0\t",
        "%conduct\tidle\t1700000010\t/work\tconductor\t0\t",
        "%2\tactive\t1700000020\t/repo/b\tmain\t1\t",
    ])

    def fake_run_tmux(*args, check=True):
        if args[0] == "list-panes":
            return list_panes_output
        return ""

    original_run_tmux = tmux.run_tmux
    original_running = tmux.get_running_claude_pane_ids
    original_session = tmux._get_conductor_session

    try:
        tmux.run_tmux = fake_run_tmux
        tmux.get_running_claude_pane_ids = lambda: {"%1", "%conduct", "%2"}
        tmux._get_conductor_session = lambda: "conductor"

        panes = tmux.get_hop_panes(validate=True)
        ids = [p.id for p in panes]

        results.append(TestResult(
            "conductor_session_excluded__no_conductor_pane",
            "%conduct" not in ids,
            f"Expected %conduct to be filtered, got {ids}",
        ))
        results.append(TestResult(
            "conductor_session_excluded__other_panes_kept",
            "%1" in ids and "%2" in ids,
            f"Expected %1 and %2 to survive, got {ids}",
        ))
    finally:
        tmux.run_tmux = original_run_tmux
        tmux.get_running_claude_pane_ids = original_running
        tmux._get_conductor_session = original_session

    return results


def test_spawn_window_session_target_disambiguation() -> list[TestResult]:
    """`spawn_window` must target sessions as `name:` to avoid window-name collisions.

    Regression for: when a window in another session shares the spawn-task
    `--session` argument's name, `tmux -t <name>` resolves to that window and
    `new-window` fails with "index N in use". Forcing the trailing colon
    makes the target unambiguous (session-only).
    """
    from . import tmux

    results = []

    # Case A: session already exists → new-window path.
    calls_a: list[tuple] = []

    def fake_run_a(*args, check=True):
        calls_a.append(args)
        if args[0] == "new-window":
            return "@42"
        return ""

    original_run = tmux.run_tmux
    original_has = tmux.has_session
    original_sleep = tmux.time.sleep
    try:
        tmux.run_tmux = fake_run_a
        tmux.has_session = lambda name: True
        tmux.time.sleep = lambda _: None

        tmux.spawn_window(
            session="my-terminal",
            cwd="/tmp",
            prompt="",
            window_name=None,
            switch=False,
        )

        new_window_calls = [c for c in calls_a if c and c[0] == "new-window"]
        ok = (
            len(new_window_calls) == 1
            and "-t" in new_window_calls[0]
            and new_window_calls[0][new_window_calls[0].index("-t") + 1] == "my-terminal:"
        )
        results.append(TestResult(
            "spawn_window_session_target__new_window_uses_colon",
            ok,
            f"Expected new-window -t my-terminal:, got {new_window_calls}",
        ))
    finally:
        tmux.run_tmux = original_run
        tmux.has_session = original_has
        tmux.time.sleep = original_sleep

    # Case B: session missing → spawn_session + display-message path.
    calls_b: list[tuple] = []

    def fake_run_b(*args, check=True):
        calls_b.append(args)
        if args[0] == "display-message":
            return "@99"
        return ""

    try:
        tmux.run_tmux = fake_run_b
        tmux.has_session = lambda name: False
        tmux.time.sleep = lambda _: None

        tmux.spawn_window(
            session="my-terminal",
            cwd="/tmp",
            prompt="",
            window_name=None,
            switch=False,
        )

        send_keys_calls = [c for c in calls_b if c and c[0] == "send-keys"]
        display_msg_calls = [c for c in calls_b if c and c[0] == "display-message"]

        first_send = send_keys_calls[0] if send_keys_calls else ()
        send_target_ok = (
            "-t" in first_send
            and first_send[first_send.index("-t") + 1] == "my-terminal:"
        )
        results.append(TestResult(
            "spawn_window_session_target__send_keys_uses_colon",
            send_target_ok,
            f"Expected send-keys -t my-terminal:, got {first_send}",
        ))

        display_ok = (
            len(display_msg_calls) >= 1
            and "-t" in display_msg_calls[0]
            and display_msg_calls[0][display_msg_calls[0].index("-t") + 1] == "my-terminal:"
        )
        results.append(TestResult(
            "spawn_window_session_target__display_message_uses_colon",
            display_ok,
            f"Expected display-message -t my-terminal:, got {display_msg_calls}",
        ))
    finally:
        tmux.run_tmux = original_run
        tmux.has_session = original_has
        tmux.time.sleep = original_sleep

    return results


def test_register_arg_parsing() -> list[TestResult]:
    """`register` parses --state/--reason into the expected namespace."""
    results = []

    parser = _build_full_parser()

    ns = parser.parse_args(["register", "--state", "waiting", "--reason", "permission"])
    results.append(TestResult(
        "register_arg_parsing__state_and_reason",
        ns.state == "waiting" and ns.reason == "permission",
        f"Unexpected namespace: {ns!r}",
    ))

    ns = parser.parse_args(["register", "--state", "idle"])
    results.append(TestResult(
        "register_arg_parsing__reason_defaults_empty",
        ns.reason == "",
        f"Expected empty default reason, got {ns.reason!r}",
    ))

    return results


def test_state_icon_from_status_format() -> list[TestResult]:
    """Window-rename icons honor @hop-status-format tokens with STATE_ICONS fallback."""
    from . import cli

    results = []

    original_get_global_option = cli.get_global_option
    try:
        cli.get_global_option = lambda name, default="": "{waiting:W} {idle:I}"
        results.append(TestResult(
            "state_icon__from_format_token",
            cli._get_state_icon("waiting") == "W",
            f"Expected 'W', got {cli._get_state_icon('waiting')!r}",
        ))
        results.append(TestResult(
            "state_icon__fallback_when_token_missing",
            cli._get_state_icon("active") == cli.STATE_ICONS["active"],
            f"Expected fallback icon, got {cli._get_state_icon('active')!r}",
        ))

        cli.get_global_option = lambda name, default="": default
        results.append(TestResult(
            "state_icon__default_format",
            cli._get_state_icon("waiting") == cli.STATE_ICONS["waiting"],
            f"Expected default icon, got {cli._get_state_icon('waiting')!r}",
        ))
    finally:
        cli.get_global_option = original_get_global_option

    return results


def test_best_window_state() -> list[TestResult]:
    """Window icon aggregation picks the highest-priority state among panes."""
    from . import cli

    results = []

    results.append(TestResult(
        "best_window_state__waiting_wins",
        cli._best_window_state(["active", "waiting", "idle"], "active") == "waiting",
        "Expected waiting to win over idle/active",
    ))
    results.append(TestResult(
        "best_window_state__idle_over_active",
        cli._best_window_state(["active", "idle"], "active") == "idle",
        "Expected idle to win over active",
    ))
    results.append(TestResult(
        "best_window_state__unknown_skipped",
        cli._best_window_state(["bogus", "active"], "waiting") == "active",
        "Expected unknown states to be skipped",
    ))
    results.append(TestResult(
        "best_window_state__empty_falls_back",
        cli._best_window_state([], "idle") == "idle",
        "Expected fallback on empty query",
    ))

    return results


def test_notify_dedup_cooldown() -> list[TestResult]:
    """Duplicate notifications within the cooldown are detected via the pane stamp."""
    import time as _time

    from . import notify

    results = []

    fp = notify._notify_fingerprint("proj (waiting): some message")
    now = int(_time.time())

    original_get_pane_option = notify.get_pane_option
    try:
        notify.get_pane_option = lambda name, pane_id=None: f"{now}:{fp}"
        results.append(TestResult(
            "notify_dedup__same_fingerprint_within_cooldown",
            notify._is_duplicate_notification("%1", fp),
            "Expected duplicate within cooldown",
        ))
        results.append(TestResult(
            "notify_dedup__different_fingerprint_passes",
            not notify._is_duplicate_notification("%1", "other"),
            "Different fingerprint must not dedup",
        ))

        stale = now - notify.NOTIFY_COOLDOWN_SECONDS - 1
        notify.get_pane_option = lambda name, pane_id=None: f"{stale}:{fp}"
        results.append(TestResult(
            "notify_dedup__expired_stamp_passes",
            not notify._is_duplicate_notification("%1", fp),
            "Expired stamp must not dedup",
        ))

        notify.get_pane_option = lambda name, pane_id=None: ""
        results.append(TestResult(
            "notify_dedup__no_stamp_passes",
            not notify._is_duplicate_notification("%1", fp),
            "Missing stamp must not dedup",
        ))
    finally:
        notify.get_pane_option = original_get_pane_option

    return results


def test_set_pane_state_transition() -> list[TestResult]:
    """set_pane_state reports whether the state actually changed.

    The prior @hop-state is read via a leading show-option in the same tmux
    invocation; auto-hop / app-focus gate on the returned flag so a re-asserted
    state (e.g. idle_prompt after Stop) doesn't re-yank focus to a left pane.
    """
    from . import tmux

    results = []
    prev = {"value": ""}
    captured: dict = {}

    def fake_run_tmux(*args, check=True):
        captured["args"] = args
        return prev["value"]

    original = tmux.run_tmux
    try:
        tmux.run_tmux = fake_run_tmux

        prev["value"] = ""
        changed = tmux.set_pane_state("waiting", "%1", reason="permission")
        results.append(TestResult(
            "set_pane_state__first_register_is_change",
            changed is True,
            f"Expected True for first-ever register, got {changed}",
        ))
        results.append(TestResult(
            "set_pane_state__reads_prior_in_same_call",
            captured["args"][:2] == ("show-option", "-pqv"),
            f"Expected leading show-option read, got {captured['args'][:4]}",
        ))

        prev["value"] = "idle"
        results.append(TestResult(
            "set_pane_state__same_state_not_a_change",
            tmux.set_pane_state("idle", "%1") is False,
            "Re-asserting idle must report no change",
        ))

        prev["value"] = "active"
        results.append(TestResult(
            "set_pane_state__transition_is_change",
            tmux.set_pane_state("idle", "%1") is True,
            "active→idle must report a change",
        ))

        prev["value"] = "idle\n"
        results.append(TestResult(
            "set_pane_state__strips_output_whitespace",
            tmux.set_pane_state("idle", "%1") is False,
            "Trailing tmux output whitespace must not count as a change",
        ))
    finally:
        tmux.run_tmux = original

    return results


def test_git_identity() -> list[TestResult]:
    """`get_git_identity` resolves branch + main-repo name, also from worktrees."""
    import subprocess

    from .tmux import get_git_identity

    results = []

    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir) / "myrepo"
        repo.mkdir()
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        try:
            subprocess.run([*git, "init", "-q"], cwd=repo, check=True, capture_output=True)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "init"],
                           cwd=repo, check=True, capture_output=True)
            subprocess.run([*git, "branch", "-M", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run([*git, "worktree", "add", "-b", "feature/x", str(Path(tmpdir) / "wt")],
                           cwd=repo, check=True, capture_output=True)
            subprocess.run([*git, "worktree", "add", "--detach", str(Path(tmpdir) / "wt-headonly")],
                           cwd=repo, check=True, capture_output=True)
            short_sha = subprocess.run([*git, "rev-parse", "--short", "HEAD"],
                                       cwd=repo, check=True, capture_output=True,
                                       text=True).stdout.strip()
        except (subprocess.SubprocessError, OSError) as e:
            return [TestResult("git_identity__fixture", False, f"git fixture failed: {e}")]

        results.append(TestResult(
            "git_identity__main_checkout",
            get_git_identity(str(repo)) == ("main", "myrepo"),
            f"Expected ('main', 'myrepo'), got {get_git_identity(str(repo))}",
        ))
        results.append(TestResult(
            "git_identity__linked_worktree_resolves_main_repo",
            get_git_identity(str(Path(tmpdir) / "wt")) == ("feature/x", "myrepo"),
            f"Expected ('feature/x', 'myrepo'), got {get_git_identity(str(Path(tmpdir) / 'wt'))}",
        ))
        results.append(TestResult(
            "git_identity__detached_worktree_uses_dir_name",
            get_git_identity(str(Path(tmpdir) / "wt-headonly")) == ("wt-headonly", "myrepo"),
            f"Expected ('wt-headonly', 'myrepo'), got {get_git_identity(str(Path(tmpdir) / 'wt-headonly'))}",
        ))

        subprocess.run([*git, "checkout", "-q", "--detach"], cwd=repo, check=False, capture_output=True)
        results.append(TestResult(
            "git_identity__detached_main_uses_short_sha",
            get_git_identity(str(repo)) == (f"@{short_sha}", "myrepo"),
            f"Expected ('@{short_sha}', 'myrepo'), got {get_git_identity(str(repo))}",
        ))

        results.append(TestResult(
            "git_identity__outside_repo_is_empty",
            get_git_identity(tempfile.gettempdir()) == ("", ""),
            f"Expected ('', ''), got {get_git_identity(tempfile.gettempdir())}",
        ))

    return results


def test_inbox_lines_alignment() -> list[TestResult]:
    """`_format_inbox_lines` pads columns, drops empty ones, colors on demand."""
    import time

    from . import cli
    from .tmux import PaneInfo

    results = []
    now = int(time.time())

    # Pin the icon source so the test is independent of the user's
    # @hop-status-format tmux option.
    original_get_global_option = cli.get_global_option
    cli.get_global_option = lambda name, default="": default
    try:
        entries = [
            PaneInfo(
                id="%1", state="waiting", timestamp=now - 300, cwd="/repo/proj",
                session="main", window=1, task="fix bug", wait_reason="question",
                repo="proj", branch="feature/login",
            ),
            PaneInfo(
                id="%2", state="idle", timestamp=now - 300, cwd="/repo/longer-project",
                session="work", window=12, task="do stuff", wait_reason="",
                repo="longer-project", branch="hotfix/x",
            ),
        ]
        lines = cli._format_inbox_lines(entries)
        labels = [line.split("\t")[0] for line in lines]
        pane_ids = [line.split("\t")[1] for line in lines]

        results.append(TestResult(
            "inbox_lines__pane_id_field",
            pane_ids == ["%1", "%2"],
            f"Expected ['%1', '%2'], got {pane_ids}",
        ))
        results.append(TestResult(
            "inbox_lines__branch_columns_align",
            labels[0].index("feature/login") == labels[1].index("hotfix/x"),
            f"Branch columns misaligned: {labels}",
        ))
        results.append(TestResult(
            "inbox_lines__task_columns_align",
            labels[0].index("fix bug") == labels[1].index("do stuff"),
            f"Task columns misaligned: {labels}",
        ))
        results.append(TestResult(
            "inbox_lines__plain_has_no_ansi",
            "\033" not in lines[0] and "\033" not in lines[1],
            f"Plain output must not contain ANSI codes: {labels}",
        ))

        ansi_lines = cli._format_inbox_lines(entries, use_ansi=True)
        results.append(TestResult(
            "inbox_lines__ansi_colors_state_icon",
            cli.STATE_ANSI["waiting"] in ansi_lines[0]
            and cli.STATE_ANSI["idle"] in ansi_lines[1]
            and cli.ANSI_RESET in ansi_lines[0],
            f"ANSI output missing state colors: {ansi_lines}",
        ))

        # A column empty across all entries is dropped entirely.
        single = [PaneInfo(
            id="%9", state="idle", timestamp=now - 300, cwd="",
            session="work", window=2, repo="demo",
        )]
        line = cli._format_inbox_lines(single)[0]
        results.append(TestResult(
            "inbox_lines__empty_columns_dropped",
            line == "󰄬  work:2  demo  5m\t%9",
            f"Expected empty branch/reason/task columns dropped, got {line!r}",
        ))
    finally:
        cli.get_global_option = original_get_global_option

    return results


def test_status_inbox_line() -> list[TestResult]:
    """`status-inbox` renders pending panes as clickable `<icon> <dir>` badges."""
    from . import cli
    from .tmux import PaneInfo

    results = []
    now = 1700000000

    originals = {
        name: getattr(cli, name)
        for name in ("get_hop_panes", "get_global_option", "is_in_tmux", "option_is_set")
    }
    # Pin icon source to DEFAULT_STATUS_FORMAT and cleared-at to 0; no style
    # override set, so badges use the STATE_TMUX_BADGE defaults.
    cli.get_global_option = lambda name, default="": default
    cli.is_in_tmux = lambda: True
    cli.option_is_set = lambda name: False
    try:
        wi = cli._get_state_icon("waiting")
        ii = cli._get_state_icon("idle")

        panes = [
            PaneInfo("%1", "idle", now, "/repo/myapp", "work", 1),
            PaneInfo("%2", "waiting", now - 60, "/repo/palm", "main", 2),
            PaneInfo("%3", "active", now, "/repo/busy", "main", 3),
        ]
        cli.get_hop_panes = lambda validate=True: list(panes)
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            rc = cli.cmd_status_inbox(argparse.Namespace())
        out = buf.getvalue()

        results.append(TestResult(
            "status_inbox__exit_zero", rc == 0, f"Expected 0, got {rc}",
        ))
        # waiting first (yellow badge), then idle (green badge); active excluded.
        expected = (
            f"#[range=pane|%2 fg=colour235 bg=colour143] {wi} palm #[norange default]"
            f" #[range=pane|%1 fg=colour235 bg=colour108] {ii} myapp #[norange default]"
        )
        results.append(TestResult(
            "status_inbox__order_color_active_excluded",
            out == expected,
            f"Expected {expected!r}, got {out!r}",
        ))

        # Explicit empty style override disables color: plain badge, still
        # wrapped in range=pane. option_is_set distinguishes it from unset.
        style_opts = set(cli.INBOX_STYLE_OPTIONS.values())
        cli.option_is_set = lambda name: name in style_opts
        cli.get_global_option = lambda name, default="": "" if name in style_opts else default
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            cli.cmd_status_inbox(argparse.Namespace())
        out = buf.getvalue()
        expected_plain = (
            f"#[range=pane|%2] {wi} palm #[norange default]"
            f" #[range=pane|%1] {ii} myapp #[norange default]"
        )
        results.append(TestResult(
            "status_inbox__style_override_disables_color",
            out == expected_plain,
            f"Expected {expected_plain!r}, got {out!r}",
        ))
        cli.get_global_option = lambda name, default="": default
        cli.option_is_set = lambda name: False

        cli.get_hop_panes = lambda validate=True: [
            PaneInfo("%9", "active", now, "/repo/x", "main", 1),
        ]
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            cli.cmd_status_inbox(argparse.Namespace())
        results.append(TestResult(
            "status_inbox__empty_when_no_pending",
            buf.getvalue() == "",
            f"Expected empty output, got {buf.getvalue()!r}",
        ))

        many = [
            PaneInfo(f"%{i}", "idle", now - i, f"/repo/p{i}", "s", i)
            for i in range(15)
        ]
        cli.get_hop_panes = lambda validate=True: list(many)
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            cli.cmd_status_inbox(argparse.Namespace())
        out = buf.getvalue()
        results.append(TestResult(
            "status_inbox__lists_all_no_cap",
            out.count("range=pane|") == 15,
            f"Expected all 15 segments (tmux truncates), got {out.count('range=pane|')}",
        ))
    finally:
        for name, val in originals.items():
            setattr(cli, name, val)

    return results


def _patch_cmd_inbox_env(cli, panes, running, cleared):
    """Patch the cli attributes `cmd_inbox` touches; returns the originals."""
    originals = {
        name: getattr(cli, name)
        for name in ("get_hop_panes", "get_running_claude_pane_ids",
                     "validate_waiting_panes", "clear_pane_state",
                     "get_global_option", "LEGACY_INBOX_FILE")
    }
    cli.get_hop_panes = lambda validate=True: panes
    cli.get_running_claude_pane_ids = lambda: running
    cli.validate_waiting_panes = lambda panes: None
    cli.clear_pane_state = lambda pane_id=None: cleared.append(pane_id)
    cli.get_global_option = lambda name, default="": default
    # Keep tests from unlinking the user's real legacy jsonl.
    cli.LEGACY_INBOX_FILE = Path(tempfile.gettempdir()) / "hop-test-legacy-inbox.jsonl"
    return originals


def test_self_heal_ps_failure() -> list[TestResult]:
    """A failed process scan (None) must not mass-clear live sessions."""
    import time

    from . import cli, tmux
    from .tmux import PaneInfo

    results = []
    now = int(time.time())

    # get_stale_panes: unknown liveness → nothing is stale.
    original_running = tmux.get_running_claude_pane_ids
    try:
        tmux.get_running_claude_pane_ids = lambda: None
        results.append(TestResult(
            "ps_failure__get_stale_panes_empty",
            tmux.get_stale_panes() == [],
            f"Expected [] on scan failure, got {tmux.get_stale_panes()}",
        ))
    finally:
        tmux.get_running_claude_pane_ids = original_running

    # cmd_inbox: killed-claude candidates can't be judged without ps —
    # every stateful pane is shown and none gets its state cleared.
    cleared: list[str] = []
    panes = [
        PaneInfo(id="%1", state="idle", timestamp=now - 60, cwd="", session="main", window=0),
        PaneInfo(id="%3", state="waiting", timestamp=now - 30, cwd="", session="main", window=1),
    ]
    originals = _patch_cmd_inbox_env(cli, panes, None, cleared)
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(argparse.Namespace(ansi=False))
        lines = [line for line in out.getvalue().splitlines() if line]

        results.append(TestResult(
            "ps_failure__keeps_all_stateful_panes",
            len(lines) == 2,
            f"Expected both panes kept on scan failure, got {lines}",
        ))
        results.append(TestResult(
            "ps_failure__no_state_cleared",
            cleared == [],
            f"Expected no clear_pane_state calls, got {cleared}",
        ))
    finally:
        for name, value in originals.items():
            setattr(cli, name, value)

    return results


def test_inbox_self_heal() -> list[TestResult]:
    """`cmd_inbox` clears state for panes whose claude was killed."""
    import time

    from . import cli
    from .tmux import PaneInfo

    results = []
    now = int(time.time())

    # %1: live pane + running claude (kept)
    # %3: pane alive with state but claude killed (cleared + hidden)
    # Gone panes need no case here — their options vanish with the pane,
    # so they never appear in get_hop_panes output at all.
    cleared: list[str] = []
    panes = [
        PaneInfo(id="%1", state="idle", timestamp=now - 60, cwd="", session="main", window=0),
        PaneInfo(id="%3", state="idle", timestamp=now - 60, cwd="", session="main", window=0),
    ]
    originals = _patch_cmd_inbox_env(cli, panes, {"%1"}, cleared)
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(argparse.Namespace(ansi=False))
        lines = [line for line in out.getvalue().splitlines() if line]

        results.append(TestResult(
            "inbox_self_heal__keeps_live_pane",
            len(lines) == 1 and lines[0].endswith("\t%1"),
            f"Expected only %1 to remain, got {lines}",
        ))
        results.append(TestResult(
            "inbox_self_heal__clears_stale_pane_state",
            cleared == ["%3"],
            f"Expected clear_pane_state for %3 only, got {cleared}",
        ))
    finally:
        for name, value in originals.items():
            setattr(cli, name, value)

    return results


def test_inbox_identity_backfill() -> list[TestResult]:
    """`cmd_inbox` resolves + persists git identity for pre-identity panes."""
    import time

    from . import cli
    from .tmux import PaneInfo

    results = []
    now = int(time.time())

    # %1: no identity options yet (pre-0.7 register) → resolved + persisted
    # %2: identity already stored → untouched
    cleared: list[str] = []
    panes = [
        PaneInfo(id="%1", state="idle", timestamp=now - 60, cwd="/wt/feature-x",
                 session="s", window=0),
        PaneInfo(id="%2", state="idle", timestamp=now - 50, cwd="/repo",
                 session="s", window=1, repo="known", branch="main"),
    ]
    resolved: list[str] = []
    persisted: list[tuple] = []
    originals = _patch_cmd_inbox_env(cli, panes, {"%1", "%2"}, cleared)
    original_resolve = cli.get_git_identity
    original_persist = cli.set_pane_git_identity
    try:
        def fake_resolve(cwd):
            resolved.append(cwd)
            return "feature/x", "myrepo"

        cli.get_git_identity = fake_resolve
        cli.set_pane_git_identity = (
            lambda repo, branch, pane_id=None: persisted.append((repo, branch, pane_id))
        )

        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(argparse.Namespace(ansi=False))
        lines = [line for line in out.getvalue().splitlines() if line]

        results.append(TestResult(
            "identity_backfill__resolves_unidentified_only",
            resolved == ["/wt/feature-x"],
            f"Expected one resolve for %1's cwd, got {resolved}",
        ))
        results.append(TestResult(
            "identity_backfill__persists_to_pane_options",
            persisted == [("myrepo", "feature/x", "%1")],
            f"Expected identity persisted for %1, got {persisted}",
        ))
        line1 = next((line for line in lines if line.endswith("\t%1")), "")
        results.append(TestResult(
            "identity_backfill__display_uses_resolved_identity",
            "myrepo" in line1 and "feature/x" in line1,
            f"Expected resolved identity in %1's row, got {line1!r}",
        ))
    finally:
        cli.get_git_identity = original_resolve
        cli.set_pane_git_identity = original_persist
        for name, value in originals.items():
            setattr(cli, name, value)

    return results


def test_inbox_includes_active() -> list[TestResult]:
    """`cmd_inbox` lists active panes after pending ones; dismiss keeps them."""
    import time

    from . import cli
    from .tmux import PaneInfo

    results = []
    now = int(time.time())

    cleared: list[str] = []
    panes = [
        PaneInfo(id="%a", state="active", timestamp=now - 10, cwd="", session="s", window=0),
        PaneInfo(id="%i", state="idle", timestamp=now - 100, cwd="", session="s", window=1),
        PaneInfo(id="%w", state="waiting", timestamp=now - 200, cwd="", session="s", window=2),
    ]
    originals = _patch_cmd_inbox_env(cli, panes, {"%a", "%i", "%w"}, cleared)
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(argparse.Namespace(ansi=False))
        ids = [line.split("\t")[1] for line in out.getvalue().splitlines() if line]
        results.append(TestResult(
            "inbox_active__priority_order",
            ids == ["%w", "%i", "%a"],
            f"Expected waiting → idle → active, got {ids}",
        ))

        # Dismissing hides the pending entries but never the active overview.
        cli.get_global_option = (
            lambda name, default="": str(now) if name == cli.INBOX_CLEARED_OPTION else default
        )
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_inbox(argparse.Namespace(ansi=False))
        ids = [line.split("\t")[1] for line in out.getvalue().splitlines() if line]
        results.append(TestResult(
            "inbox_active__survives_dismiss",
            ids == ["%a"],
            f"Expected only the active pane after dismiss, got {ids}",
        ))
    finally:
        for name, value in originals.items():
            setattr(cli, name, value)

    return results


def test_pending_panes() -> list[TestResult]:
    """`_pending_panes` filters to pending states and honors the dismiss stamp."""
    import time

    from . import cli
    from .tmux import PaneInfo

    results = []
    now = int(time.time())

    panes = [
        PaneInfo(id="%a", state="active", timestamp=now - 10, cwd="", session="s", window=0),
        PaneInfo(id="%i", state="idle", timestamp=now - 100, cwd="", session="s", window=1),
        PaneInfo(id="%w-old", state="waiting", timestamp=now - 200, cwd="", session="s", window=2),
        PaneInfo(id="%w-new", state="waiting", timestamp=now - 50, cwd="", session="s", window=3),
    ]

    original_get_global_option = cli.get_global_option
    try:
        cli.get_global_option = lambda name, default="": default
        ids = [p.id for p in cli._pending_panes(panes)]
        results.append(TestResult(
            "pending_panes__priority_order_excludes_active",
            ids == ["%w-new", "%w-old", "%i"],
            f"Expected waiting (newest first) then idle, got {ids}",
        ))

        # Dismiss stamp hides panes whose timestamp predates it; a later
        # state change (newer timestamp) resurfaces the pane.
        cli.get_global_option = lambda name, default="": str(now - 150)
        ids = [p.id for p in cli._pending_panes(panes)]
        results.append(TestResult(
            "pending_panes__dismiss_stamp_filters",
            ids == ["%w-new", "%i"],
            f"Expected %w-old dismissed, got {ids}",
        ))

        # Unparsable stamp falls back to showing everything.
        cli.get_global_option = lambda name, default="": "garbage"
        ids = [p.id for p in cli._pending_panes(panes)]
        results.append(TestResult(
            "pending_panes__bad_stamp_ignored",
            ids == ["%w-new", "%w-old", "%i"],
            f"Expected full pending list on bad stamp, got {ids}",
        ))
    finally:
        cli.get_global_option = original_get_global_option

    return results


def run_all_tests() -> tuple[list[TestResult], int, int]:
    """Run all tests and return (results, passed, failed)."""
    all_results: list[TestResult] = []
    all_results.extend(test_state_transitions())
    all_results.extend(test_priority_sorting())
    all_results.extend(test_dialog_detection())
    all_results.extend(test_terminal_detection())
    all_results.extend(test_macos_terminal_app_from_process_tree())
    all_results.extend(test_terminal_detection_prefers_tmux_client())
    all_results.extend(test_macos_focus_behaviors())
    all_results.extend(test_pane_context_resolution())
    all_results.extend(test_normalize_task())
    all_results.extend(test_extract_task_from_transcript())
    all_results.extend(test_git_identity())
    all_results.extend(test_inbox_lines_alignment())
    all_results.extend(test_status_inbox_line())
    all_results.extend(test_inbox_self_heal())
    all_results.extend(test_self_heal_ps_failure())
    all_results.extend(test_inbox_identity_backfill())
    all_results.extend(test_inbox_includes_active())
    all_results.extend(test_pending_panes())
    all_results.extend(test_cmd_list_json())
    all_results.extend(test_register_arg_parsing())
    all_results.extend(test_state_icon_from_status_format())
    all_results.extend(test_best_window_state())
    all_results.extend(test_notify_dedup_cooldown())
    all_results.extend(test_set_pane_state_transition())
    all_results.extend(test_spawn_task_arg_parsing())
    all_results.extend(test_send_prompt_arg_parsing())
    all_results.extend(test_conductor_arg_parsing())
    all_results.extend(test_update_instructions())
    all_results.extend(test_conductor_context())
    all_results.extend(test_conductor_prompt_context())
    all_results.extend(test_send_prompt_blocks_active_pane())
    all_results.extend(test_conductor_session_excluded())
    all_results.extend(test_spawn_window_session_target_disambiguation())
    all_results.extend(validate_hooks_json())

    passed = sum(1 for r in all_results if r.passed)
    failed = len(all_results) - passed

    return all_results, passed, failed
