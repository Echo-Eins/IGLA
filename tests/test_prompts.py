"""Planner prompt construction — three-layer composer."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from igla.ids import prefixed_id
from igla.planner.llm_client import LLMChatMessage
from igla.planner.prompts import (
    SESSION_HARD_RULES,
    TOOL_USAGE_EXAMPLES,
    build_proposal_messages,
    build_proposal_schema,
    build_session_system_message,
    build_task_system_message,
    build_turn_user_message,
    tools_digest_from_registry_dump,
)
from igla.protocol.policy import (
    ActionRequest,
    PolicyDecision,
    PolicyDecisionKind,
    PolicyRejection,
)
from igla.protocol.runtime import RuntimeMode, RuntimeStateSnapshot
from igla.protocol.task import TaskSpec, TaskStatus
from igla.todo.tree import TodoTree


def _runtime_snapshot(task_id: str, mode: RuntimeMode = RuntimeMode.READY) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        task_id=task_id,
        mode=mode,
        iteration=1,
        consecutive_rejections=0,
        allowed_next_actions=["tool:find_files", "tool:search_text"],
        forbidden_next_actions=[],
        captured_at=datetime(2026, 4, 29, tzinfo=UTC),
    )


def _new_task() -> TaskSpec:
    return TaskSpec(
        task_id=prefixed_id("task"),
        raw_request="Найди и открой 00-review.md",
        goal="Найди и открой 00-review.md",
        status=TaskStatus.READY,
        created_at=datetime(2026, 4, 29, tzinfo=UTC),
    )


def test_build_proposal_messages_returns_three_layers(step_clock) -> None:
    task = _new_task()
    todo = TodoTree(task.task_id, step_clock)
    todo.create_root(title=task.goal)
    runtime = _runtime_snapshot(task.task_id)

    messages = build_proposal_messages(
        task=task,
        runtime=runtime,
        todo=todo.snapshot(),
        last_decision=None,
        new_events=[],
        available_tools=[
            {"name": "find_files", "version": "1.0.0", "description": "find files"},
            {"name": "search_text", "version": "1.0.0", "description": "grep text"},
        ],
    )
    assert len(messages) == 3
    assert messages[0].role == "system"
    assert messages[1].role == "system"
    assert messages[2].role == "user"


def test_session_system_message_contains_rules_and_tools() -> None:
    msg = build_session_system_message(
        tools_digest_from_registry_dump(
            [
                {
                    "name": "find_files",
                    "version": "1.0.0",
                    "description": "find files",
                    "input_schema": {"type": "object"},
                    "risk_level": "read_only",
                    "side_effects": False,
                }
            ]
        )
    )
    assert msg.role == "system"
    body = msg.content
    assert "ROLE" in body
    assert "HARD RULES" in body
    assert "OUTPUT GRAMMAR" in body
    assert "TOOLS" in body
    # Each hard rule appears verbatim.
    for rule in SESSION_HARD_RULES:
        # Use the first 30 chars to guard against accidental rewording in fmt.
        assert rule[:30] in body
    # Tool examples for find_files inlined.
    for example in TOOL_USAGE_EXAMPLES["find_files"]:
        assert example["intent"][:20] in body


def test_session_system_message_extra_sections() -> None:
    msg = build_session_system_message(
        [],
        extra_sections=[("Project Conventions", "Use only Python 3.11+.")],
    )
    assert "PROJECT CONVENTIONS" in msg.content
    assert "Use only Python 3.11+." in msg.content


def test_task_system_message_carries_task_identity() -> None:
    task = _new_task()
    msg = build_task_system_message(task)
    assert msg.role == "system"
    assert msg.content.startswith("TASK\n")
    body = json.loads(msg.content[len("TASK\n") :])
    assert body["task_id"] == task.task_id
    assert body["goal"] == task.goal
    assert body["raw_request"] == task.raw_request
    assert "constraints" in body
    assert "success_criteria" in body


def test_turn_user_message_is_compact_delta(step_clock) -> None:
    task = _new_task()
    todo = TodoTree(task.task_id, step_clock)
    todo.create_root(title=task.goal)
    runtime = _runtime_snapshot(task.task_id, mode=RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED)

    msg = build_turn_user_message(
        runtime=runtime,
        todo=todo.snapshot(),
        last_decision=None,
        new_events=[{"kind": "tool_invocation_failed", "actor": "tool:find_files"}],
    )
    assert msg.role == "user"
    payload = json.loads(msg.content)
    assert payload["iteration"] == 1
    assert payload["mode"] == "failure_diagnosis_required"
    assert payload["allowed_next_actions"] == ["tool:find_files", "tool:search_text"]
    assert "todo_summary" in payload
    assert payload["new_events_since_last_turn"][0]["kind"] == "tool_invocation_failed"
    # No mention of full tool catalog or task spec — those live in system layers.
    assert "task_id" not in payload
    assert "available_tools" not in payload


def test_turn_user_message_includes_rejection_when_present(step_clock) -> None:
    task = _new_task()
    todo = TodoTree(task.task_id, step_clock)
    todo.create_root(title=task.goal)
    runtime = _runtime_snapshot(task.task_id)
    decision = PolicyDecision(
        decision=PolicyDecisionKind.DENY,
        action=ActionRequest(
            kind="tool_invocation",
            actor="planner",
            task_id=task.task_id,
            reason="x",
            input={},
        ),
        rejection=PolicyRejection(
            reason_code="UNKNOWN_TOOL",
            message="tool not found",
            rule_id="registry.lookup",
        ),
        allowed_next_actions=["tool_invocation"],
        forbidden_next_actions=[],
    )

    msg = build_turn_user_message(
        runtime=runtime,
        todo=todo.snapshot(),
        last_decision=decision,
        new_events=None,
    )
    payload = json.loads(msg.content)
    assert payload["last_rejection"]["reason_code"] == "UNKNOWN_TOOL"


def test_proposal_messages_uses_caches(step_clock) -> None:
    """Caller-supplied cached messages bypass the per-turn rebuild."""
    task = _new_task()
    todo = TodoTree(task.task_id, step_clock)
    todo.create_root(title=task.goal)
    runtime = _runtime_snapshot(task.task_id)
    cached_session = LLMChatMessage(role="system", content="<<cached-session>>")
    cached_task = LLMChatMessage(role="system", content="<<cached-task>>")

    messages = build_proposal_messages(
        task=task,
        runtime=runtime,
        todo=todo.snapshot(),
        last_decision=None,
        new_events=[],
        available_tools=[],
        cached_session_message=cached_session,
        cached_task_message=cached_task,
    )
    assert messages[0].content == "<<cached-session>>"
    assert messages[1].content == "<<cached-task>>"


def test_legacy_keyword_alias_still_works(step_clock) -> None:
    """``last_event_log_tail`` is the pre-rewrite name; keep it working."""
    task = _new_task()
    todo = TodoTree(task.task_id, step_clock)
    todo.create_root(title=task.goal)
    runtime = _runtime_snapshot(task.task_id)

    messages = build_proposal_messages(
        task=task,
        runtime=runtime,
        todo=todo.snapshot(),
        last_decision=None,
        last_event_log_tail=[{"kind": "task_created", "actor": "runtime"}],
        available_tools=[],
    )
    payload = json.loads(messages[2].content)
    assert payload["new_events_since_last_turn"][0]["kind"] == "task_created"


def test_proposal_schema_requires_action_discriminator() -> None:
    schema = build_proposal_schema()
    defs = schema["$defs"]
    proposal_names = [
        "ToolInvocationProposal",
        "AskUserClarificationProposal",
        "TodoBranchProposal",
        "TodoCompleteProposal",
        "DeclareTaskDoneProposal",
    ]
    for name in proposal_names:
        proposal = defs[name]
        assert "action" in proposal["required"]
        assert "default" not in proposal["properties"]["action"]
