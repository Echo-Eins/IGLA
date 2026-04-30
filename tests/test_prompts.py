"""Planner prompt construction."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from igla.ids import prefixed_id
from igla.planner.prompts import build_proposal_messages
from igla.protocol.runtime import RuntimeMode, RuntimeStateSnapshot
from igla.protocol.task import TaskSpec, TaskStatus
from igla.todo.tree import TodoTree


def test_proposal_messages_do_not_send_system_prompt(step_clock) -> None:
    task = TaskSpec(
        task_id=prefixed_id("task"),
        raw_request="Открой файл 00-review.md",
        goal="Открой файл 00-review.md",
        status=TaskStatus.READY,
        created_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
    )
    todo = TodoTree(task.task_id, step_clock)
    todo.create_root(title=task.goal)
    runtime = RuntimeStateSnapshot(
        task_id=task.task_id,
        mode=RuntimeMode.READY,
        iteration=1,
        consecutive_rejections=0,
        allowed_next_actions=["tool:find_files"],
        forbidden_next_actions=[],
        captured_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
    )

    messages = build_proposal_messages(
        task=task,
        runtime=runtime,
        todo=todo.snapshot(),
        last_decision=None,
        last_event_log_tail=[],
        available_tools=[{"name": "find_files", "version": "1.0.0"}],
    )

    assert len(messages) == 1
    assert messages[0].role == "user"
    payload = json.loads(messages[0].content)
    assert payload["request"]["output"] == "PlannerProposal"
    assert "\n  \"" not in messages[0].content
