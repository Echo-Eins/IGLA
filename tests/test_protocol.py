"""Sanity checks for the frozen protocol envelopes."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from igla.protocol import (
    ActionRequest,
    AskUserClarificationProposal,
    DeclareTaskDoneProposal,
    PlannerProposal,
    PolicyDecision,
    PolicyDecisionKind,
    TodoBranchProposal,
    TodoCompleteProposal,
    ToolInvocation,
    ToolInvocationProposal,
    ToolRef,
    ToolResult,
    ToolError,
)


def test_envelopes_are_frozen() -> None:
    invocation = ToolInvocation(
        invocation_id="inv_x",
        task_id="task_x",
        tool=ToolRef(name="ask_user", version="1.0.0"),
    )
    with pytest.raises(ValidationError):
        invocation.task_id = "other"  # type: ignore[misc]


def test_invocation_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ToolInvocation(
            invocation_id="inv_x",
            task_id="task_x",
            tool=ToolRef(name="ask_user", version="1.0.0"),
            unknown="boom",
        )


def test_tool_result_failure_envelope() -> None:
    result = ToolResult(
        invocation_id="inv_x",
        task_id="task_x",
        status="failed",
        error=ToolError(kind="ValidationError", code="X", message="m"),
    )
    assert result.status == "failed"
    assert result.error and result.error.code == "X"


def test_proposal_discriminator_picks_right_class() -> None:
    raw = {
        "action": "tool_invocation",
        "tool_name": "ask_user",
        "tool_version": "1.0.0",
        "input": {"question": "hi?"},
        "reason": "ask the user",
    }
    proposal = PlannerProposal.model_validate(raw)
    assert isinstance(proposal.root, ToolInvocationProposal)
    assert proposal.root.tool_name == "ask_user"


@pytest.mark.parametrize(
    "raw",
    [
        {
            "action": "ask_user_clarification",
            "question": "what do you want?",
            "reason": "clarify",
        },
        {
            "action": "todo_branch",
            "parent_node_id": "todo_root",
            "reason": "split",
            "children": [{"title": "step", "kind": "subgoal"}],
        },
        {
            "action": "todo_complete",
            "node_id": "todo_x",
            "reason": "done",
        },
        {
            "action": "declare_task_done",
            "summary": "all set",
            "reason": "complete",
        },
    ],
)
def test_other_proposal_kinds(raw: dict) -> None:
    proposal = PlannerProposal.model_validate(raw)
    assert proposal.kind == raw["action"]


def test_proposal_rejects_missing_reason() -> None:
    with pytest.raises(ValidationError):
        PlannerProposal.model_validate(
            {
                "action": "declare_task_done",
                "summary": "ok",
            }
        )


def test_action_request_serialises() -> None:
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="task_x",
        tool_name="ask_user",
        tool_version="1.0.0",
        input={"question": "?"},
    )
    payload = json.loads(action.model_dump_json())
    assert payload["kind"] == "tool_invocation"


def test_policy_decision_helpers() -> None:
    decision = PolicyDecision(
        decision=PolicyDecisionKind.ALLOW,
        action=ActionRequest(kind="tool_invocation", actor="planner", task_id="t"),
    )
    assert decision.is_allow
    assert not decision.is_deny


def test_todo_branch_requires_at_least_one_child() -> None:
    with pytest.raises(ValidationError):
        TodoBranchProposal(parent_node_id="x", reason="r", children=[])


def test_todo_complete_requires_node_id() -> None:
    with pytest.raises(ValidationError):
        TodoCompleteProposal(node_id="", reason="r")


def test_declare_done_requires_summary() -> None:
    with pytest.raises(ValidationError):
        DeclareTaskDoneProposal(summary="", reason="r")


def test_clarification_requires_question() -> None:
    with pytest.raises(ValidationError):
        AskUserClarificationProposal(question="", reason="r")


def test_event_record_round_trip() -> None:
    from igla.protocol import EventKind, EventRecord

    record = EventRecord(
        event_id="evt_x",
        kind=EventKind.TASK_CREATED,
        actor="runtime",
        timestamp=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        payload={"goal": "build"},
    )
    line = record.model_dump_json()
    decoded = EventRecord.model_validate_json(line)
    assert decoded == record
