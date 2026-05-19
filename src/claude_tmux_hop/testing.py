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


def test_inbox_entry_task_backcompat() -> list[TestResult]:
    """Inbox entries written before the task field existed must still parse."""
    from . import inbox

    results = []

    original_file = inbox.INBOX_FILE
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    try:
        # Legacy entry: no "task" key
        tmp.write(json.dumps({
            "ts": 1700000000,
            "state": "waiting",
            "project": "demo",
            "pane_id": "%1",
            "session": "main",
            "window": 0,
        }) + "\n")
        # New-format entry: has "task"
        tmp.write(json.dumps({
            "ts": 1700000010,
            "state": "idle",
            "project": "demo",
            "pane_id": "%2",
            "session": "main",
            "window": 0,
            "task": "Some task",
        }) + "\n")
        tmp.close()

        inbox.INBOX_FILE = Path(tmp.name)
        entries = inbox.get_entries(limit=10)

        by_pane = {e.pane_id: e for e in entries}
        legacy = by_pane.get("%1")
        modern = by_pane.get("%2")

        results.append(TestResult(
            "inbox_entry__legacy_has_empty_task",
            legacy is not None and legacy.task == "",
            f"Legacy entry should default task to '', got {legacy.task if legacy else 'MISSING'!r}",
        ))
        results.append(TestResult(
            "inbox_entry__modern_carries_task",
            modern is not None and modern.task == "Some task",
            f"Modern entry should carry task, got {modern.task if modern else 'MISSING'!r}",
        ))
    finally:
        inbox.INBOX_FILE = original_file
        Path(tmp.name).unlink(missing_ok=True)

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
        cmd_inbox=noop,
        cmd_inbox_clear=noop,
        cmd_install=noop,
        cmd_update=noop,
        cmd_doctor=noop,
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
    """`conductor` parses mode + resume + force flags into the expected namespace."""
    results = []

    parser = _build_full_parser()

    ns_default = parser.parse_args(["conductor"])
    results.append(TestResult(
        "conductor_arg_parsing__defaults_to_popup_mode",
        ns_default.mode == "popup" and ns_default.resume is False and ns_default.force is False,
        f"Expected mode=popup, resume=False, force=False; got {ns_default!r}",
    ))

    ns_resume = parser.parse_args(["conductor", "--popup", "--continue"])
    results.append(TestResult(
        "conductor_arg_parsing__continue_sets_resume",
        ns_resume.mode == "popup" and ns_resume.resume is True,
        f"Expected mode=popup, resume=True; got {ns_resume!r}",
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

    # --popup and --update-instructions are mutually exclusive
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


def test_inbox_skips_conductor() -> list[TestResult]:
    """`inbox.record()` early-returns for conductor-session state changes."""
    from . import inbox, tmux

    results = []

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    tmp.close()
    original_file = inbox.INBOX_FILE
    original_session = tmux._get_conductor_session

    try:
        inbox.INBOX_FILE = Path(tmp.name)
        Path(tmp.name).unlink(missing_ok=True)
        tmux._get_conductor_session = lambda: "conductor"

        inbox.record(
            state="waiting", project="x", pane_id="%9",
            session="conductor", window=0,
        )

        results.append(TestResult(
            "inbox_skips_conductor__no_file_written",
            not inbox.INBOX_FILE.exists(),
            f"Expected no inbox file, got existing={inbox.INBOX_FILE.exists()}",
        ))

        # Verify a non-conductor session still records.
        inbox.record(
            state="waiting", project="y", pane_id="%5",
            session="main", window=2,
        )
        entries = inbox.get_entries()
        results.append(TestResult(
            "inbox_skips_conductor__main_session_recorded",
            len(entries) == 1 and entries[0].pane_id == "%5",
            f"Expected one entry for %5, got {entries!r}",
        ))
    finally:
        inbox.INBOX_FILE = original_file
        tmux._get_conductor_session = original_session
        Path(tmp.name).unlink(missing_ok=True)

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
    all_results.extend(test_inbox_entry_task_backcompat())
    all_results.extend(test_cmd_list_json())
    all_results.extend(test_spawn_task_arg_parsing())
    all_results.extend(test_send_prompt_arg_parsing())
    all_results.extend(test_conductor_arg_parsing())
    all_results.extend(test_update_instructions())
    all_results.extend(test_conductor_context())
    all_results.extend(test_conductor_prompt_context())
    all_results.extend(test_send_prompt_blocks_active_pane())
    all_results.extend(test_conductor_session_excluded())
    all_results.extend(test_inbox_skips_conductor())
    all_results.extend(validate_hooks_json())

    passed = sum(1 for r in all_results if r.passed)
    failed = len(all_results) - passed

    return all_results, passed, failed
