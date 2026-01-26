"""Self-test functionality for claude-tmux-hop."""

from __future__ import annotations

import json
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
    expected_order = ["%4", "%2", "%3", "%1"]  # waiting oldest, waiting newer, idle, active
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
    expected_ids = ["%4", "%2"]  # Only waiting panes, oldest first
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

    # Try multiple locations
    possible_paths = [
        package_path.parent.parent / "hooks" / "hooks.json",
        Path.cwd() / "hooks" / "hooks.json",
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


def run_all_tests() -> tuple[list[TestResult], int, int]:
    """Run all tests and return (results, passed, failed)."""
    all_results: list[TestResult] = []
    all_results.extend(test_state_transitions())
    all_results.extend(test_priority_sorting())
    all_results.extend(test_dialog_detection())
    all_results.extend(validate_hooks_json())

    passed = sum(1 for r in all_results if r.passed)
    failed = len(all_results) - passed

    return all_results, passed, failed
