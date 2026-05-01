"""Motivation engine tests — verify declarative cycles."""
from __future__ import annotations

from datetime import UTC, datetime

from igla.kernel.clock import StepClock
from igla.kernel.kernel import Kernel
from igla.motivation.cycle import MotivationCycle
from igla.motivation.rule import load_rules
from igla.protocol.event import EventKind
from igla.protocol.runtime import RuntimeMode


def _setup(make_settings) -> tuple[Kernel, MotivationCycle]:
    settings = make_settings()
    kernel = Kernel(settings, clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC)))
    rules = load_rules(settings.paths.motivation_file)
    return kernel, MotivationCycle(rules, kernel)


def test_bootstrap_sets_allowed_actions(make_settings) -> None:
    kernel, cycle = _setup(make_settings)
    evt = kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    cycle.dispatch(evt)
    state = kernel.state.get_state("t1")
    assert "tool_invocation" in state.allowed_next_actions
    assert "declare_task_done" in state.allowed_next_actions


def test_failure_event_enters_diagnosis(make_settings) -> None:
    kernel, cycle = _setup(make_settings)
    evt0 = kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    cycle.dispatch(evt0)
    evt = kernel.events.append(
        kind=EventKind.TOOL_INVOCATION_FAILED,
        actor="tool:read_file",
        task_id="t1",
        step_id="s1",
        payload={"tool_name": "read_file", "status": "failed"},
    )
    cycle.dispatch(evt)
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED
    assert "declare_task_done" not in state.allowed_next_actions
    assert "tool:find_files" in state.allowed_next_actions
    assert "tool:search_text" in state.allowed_next_actions
    assert "declare_task_done" in state.forbidden_next_actions


def test_chunked_read_success_exits_large_file_diagnosis(make_settings) -> None:
    kernel, cycle = _setup(make_settings)
    evt0 = kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    cycle.dispatch(evt0)
    kernel.state.mark_failure("t1", "FILE_TOO_LARGE")
    cycle.dispatch(
        kernel.events.append(
            kind=EventKind.TOOL_INVOCATION_FAILED,
            actor="tool:read_file",
            task_id="t1",
            step_id="s1",
            payload={
                "tool_name": "read_file",
                "status": "failed",
                "error_code": "FILE_TOO_LARGE",
            },
        )
    )
    assert kernel.state.get_state("t1").mode is RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED

    cycle.dispatch(
        kernel.events.append(
            kind=EventKind.TOOL_INVOCATION_COMPLETED,
            actor="tool:read_file",
            task_id="t1",
            step_id="s2",
            payload={
                "tool_name": "read_file",
                "status": "success",
                "output": {
                    "start_line": 0,
                    "end_line": 1000,
                    "total_lines": 1500,
                    "end_of_file": False,
                },
            },
        )
    )

    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.READY
    assert state.last_error_code is None
    assert "declare_task_done" in state.allowed_next_actions
    assert state.forbidden_next_actions == []


def test_user_input_resumes_after_clarification(make_settings) -> None:
    kernel, cycle = _setup(make_settings)
    cycle.dispatch(
        kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    )
    # Simulate the planner having moved into NEEDS_USER_CLARIFICATION via the
    # ``ask_user`` tool path.
    cycle.dispatch(
        kernel.events.append(
            kind=EventKind.TOOL_INVOCATION_COMPLETED,
            actor="tool:ask_user",
            task_id="t1",
            payload={"tool_name": "ask_user", "status": "success"},
        )
    )
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.NEEDS_USER_CLARIFICATION
    cycle.dispatch(
        kernel.events.append(
            kind=EventKind.USER_INPUT_RECEIVED,
            actor="user",
            task_id="t1",
            payload={"text": "yes"},
        )
    )
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.READY


def test_patch_ambiguous_search_bypasses_failure_diagnosis(make_settings) -> None:
    """AMBIGUOUS_SEARCH from patch_file must return immediately to READY.

    Without the exit_failure_diagnosis_for_patch_planner_error rule the system
    deadlocks: patch_file is blocked in FAILURE_DIAGNOSIS_REQUIRED, yet the
    exit condition requires changed_condition_declared which is set only by a
    successful patch_file.
    """
    kernel, cycle = _setup(make_settings)
    cycle.dispatch(
        kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    )
    evt = kernel.events.append(
        kind=EventKind.TOOL_INVOCATION_FAILED,
        actor="tool:patch_file",
        task_id="t1",
        step_id="s1",
        payload={
            "tool_name": "patch_file",
            "status": "failed",
            "error_code": "AMBIGUOUS_SEARCH",
        },
    )
    cycle.dispatch(evt)
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.READY, f"expected READY, got {state.mode}"
    assert "tool_invocation" in state.allowed_next_actions
    assert "declare_task_done" in state.allowed_next_actions
    assert "declare_task_done" not in state.forbidden_next_actions


def test_patch_search_not_found_bypasses_failure_diagnosis(make_settings) -> None:
    kernel, cycle = _setup(make_settings)
    cycle.dispatch(
        kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    )
    evt = kernel.events.append(
        kind=EventKind.TOOL_INVOCATION_FAILED,
        actor="tool:patch_file",
        task_id="t1",
        step_id="s1",
        payload={
            "tool_name": "patch_file",
            "status": "failed",
            "error_code": "SEARCH_NOT_FOUND",
        },
    )
    cycle.dispatch(evt)
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.READY


def test_patch_world_failure_stays_in_diagnosis(make_settings) -> None:
    """HASH_MISMATCH is a world-state failure — must stay in FAILURE_DIAGNOSIS_REQUIRED."""
    kernel, cycle = _setup(make_settings)
    cycle.dispatch(
        kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    )
    evt = kernel.events.append(
        kind=EventKind.TOOL_INVOCATION_FAILED,
        actor="tool:patch_file",
        task_id="t1",
        step_id="s1",
        payload={
            "tool_name": "patch_file",
            "status": "failed",
            "error_code": "HASH_MISMATCH",
        },
    )
    cycle.dispatch(evt)
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED


def test_non_patch_tool_failure_stays_in_diagnosis(make_settings) -> None:
    """AMBIGUOUS_SEARCH from a non-patch_file tool should not trigger the bypass."""
    kernel, cycle = _setup(make_settings)
    cycle.dispatch(
        kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    )
    evt = kernel.events.append(
        kind=EventKind.TOOL_INVOCATION_FAILED,
        actor="tool:read_file",
        task_id="t1",
        step_id="s1",
        payload={
            "tool_name": "read_file",
            "status": "failed",
            "error_code": "AMBIGUOUS_SEARCH",
        },
    )
    cycle.dispatch(evt)
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED


def test_task_completed_event_marks_task_done(make_settings) -> None:
    kernel, cycle = _setup(make_settings)
    cycle.dispatch(
        kernel.events.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    )
    cycle.dispatch(
        kernel.events.append(
            kind=EventKind.TASK_COMPLETED,
            actor="planner",
            task_id="t1",
            payload={"summary": "ok"},
        )
    )
    state = kernel.state.get_state("t1")
    assert state.mode is RuntimeMode.TASK_DONE
