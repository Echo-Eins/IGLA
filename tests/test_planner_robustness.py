"""Robustness of planner against malformed LLM proposals."""
from __future__ import annotations

from datetime import UTC, datetime

from conftest import build_runtime  # type: ignore[import-not-found]
from test_planner_loop import _new_task  # type: ignore[import-not-found]

from igla.kernel.clock import StepClock
from igla.planner.planner import _coerce_proposal_shape
from igla.protocol.event import EventKind
from igla.protocol.task import TaskStatus
from igla.todo.tree import TodoTree


def test_coerce_infers_tool_invocation() -> None:
    raw = {
        "reason": "read it",
        "tool_name": "read_file",
        "tool_version": "1.0.0",
        "input": {"path": "x"},
        "expected_outputs": [],
    }
    coerced, note = _coerce_proposal_shape(raw)
    assert coerced["action"] == "tool_invocation"
    assert note and "tool_invocation" in note


def test_coerce_does_not_infer_ask_user_clarification() -> None:
    raw = {"reason": "need info", "question": "where?"}
    coerced, note = _coerce_proposal_shape(raw)
    assert "action" not in coerced
    assert note is None


def test_coerce_does_not_infer_todo_branch() -> None:
    raw = {
        "reason": "split",
        "parent_node_id": "n",
        "children": [{"title": "x", "kind": "subgoal"}],
    }
    coerced, note = _coerce_proposal_shape(raw)
    assert "action" not in coerced
    assert note is None


def test_coerce_does_not_infer_declare_task_done() -> None:
    raw = {"reason": "all set", "summary": "done"}
    coerced, note = _coerce_proposal_shape(raw)
    assert "action" not in coerced
    assert note is None


def test_coerce_keeps_existing_action() -> None:
    raw = {"action": "tool_invocation", "reason": "x"}
    coerced, note = _coerce_proposal_shape(raw)
    assert coerced is raw or coerced == raw
    assert note is None


def test_planner_recovers_from_missing_action_field(make_settings) -> None:
    """The user's real-world bug: model returned no ``action`` field."""
    settings = make_settings()
    canned = [
        # Malformed: model forgot the action.
        {
            "reason": "Need to read the requested file.",
            "tool_name": "noop_observe",  # use noop so we don't depend on FS
            "tool_version": "1.0.0",
            "input": {"note": "test"},
            "expected_outputs": [],
        },
        # Subsequent turn: well-formed declare_task_done.
        {
            "action": "declare_task_done",
            "summary": "done",
            "reason": "completed",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("read file", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="read file")

    outcome = planner.run_task(task, todo)
    # Coercion should succeed: tool_invocation runs, then declare done.
    assert outcome.status is TaskStatus.DONE
    # No POLICY_REJECTION events since coercion saved us.
    rejections = [
        e
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.POLICY_REJECTION
    ]
    assert rejections == []


def test_planner_records_rejection_for_truly_unrecoverable(make_settings) -> None:
    """If the proposal cannot be coerced into any known shape, treat it as a
    rejection rather than crashing the session."""
    settings = make_settings()
    canned = [
        # Garbage: nothing identifiable.
        {"reason": "x", "totally": "unknown"},
        {
            "action": "declare_task_done",
            "summary": "giving up",
            "reason": "abort",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("x", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="x")

    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.DONE
    rejections = [
        e
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.POLICY_REJECTION
    ]
    assert rejections, "expected at least one MALFORMED_PROPOSAL rejection"
    assert any(r.payload.get("reason_code") == "MALFORMED_PROPOSAL" for r in rejections)


def test_planner_aborts_on_repeated_malformed(make_settings) -> None:
    """Endless garbage from the LLM must not loop forever."""
    settings = make_settings()
    canned = [{"reason": "x", "totally": "unknown"} for _ in range(10)]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("x", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="x")

    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.ABORTED
    assert outcome.aborted_reason == "MAX_CONSECUTIVE_REJECTIONS"
