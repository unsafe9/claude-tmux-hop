"""Self-test functionality for claude-tmux-hop."""

from __future__ import annotations

import json
import tempfile
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
    all_results.extend(validate_hooks_json())

    passed = sum(1 for r in all_results if r.passed)
    failed = len(all_results) - passed

    return all_results, passed, failed
