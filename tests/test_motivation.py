"""Motivation engine tests — verify declarative cycles."""
from __future__ import annotations

from datetime import datetime, timezone

from igla.kernel.clock import StepClock
from igla.kernel.kernel import Kernel
from igla.motivation.cycle import MotivationCycle
from igla.motivation.rule import load_rules
from igla.protocol.event import EventKind
from igla.protocol.runtime import RuntimeMode


def _setup(make_settings) -> tuple[Kernel, MotivationCycle]:
    settings = make_settings()
    kernel = Kernel(settings, clock=StepClock(datetime(2026, 4, 29, tzinfo=timezone.utc)))
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
