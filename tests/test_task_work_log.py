"""Tests for per-task work log memory."""
from __future__ import annotations

from datetime import UTC, datetime

from igla.kernel.clock import StepClock
from igla.kernel.event_store import EventStore
from igla.kernel.task_work_log import TaskWorkLog
from igla.protocol.event import EventKind
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.tools.builtin.read_task_log import ReadTaskLogTool


def _store(tmp_path):
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=UTC), step_seconds=0.1)
    return EventStore(tmp_path / "events.jsonl", clock)


def _read_log_inv(**fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_log",
        task_id="task_test",
        tool=ToolRef(name="read_task_log", version="1.0.0"),
        input=fields,
    )


def test_work_log_summary_closes_on_task_completed(tmp_path) -> None:
    events = _store(tmp_path)
    events.append(
        kind=EventKind.TASK_CREATED,
        actor="runtime",
        task_id="task_test",
        payload={"goal": "patch file", "raw_request": "patch file"},
    )
    events.append(
        kind=EventKind.TOOL_INVOCATION_COMPLETED,
        actor="tool:read_file",
        task_id="task_test",
        step_id="step_read",
        payload={
            "tool_name": "read_file",
            "status": "success",
            "output_keys": ["path", "content"],
            "output": {"path": "/workspace/src/app.py"},
        },
    )
    events.append(
        kind=EventKind.POLICY_REJECTION,
        actor="policy",
        task_id="task_test",
        payload={"reason_code": "HASH_MISMATCH_FILE_CHANGED", "message": "re-read"},
    )
    events.append(
        kind=EventKind.TASK_COMPLETED,
        actor="planner",
        task_id="task_test",
        payload={"summary": "patched"},
    )

    log = TaskWorkLog(events)
    summary = log.get_summary("task_test")
    entries = log.get_entries("task_test")

    assert log.is_closed("task_test")
    assert summary["status"] == "done"
    assert summary["summary"] == "patched"
    assert summary["tool_calls"] == 1
    assert summary["policy_rejections"] == 1
    assert summary["files_touched"] == ["/workspace/src/app.py"]
    assert [entry["kind"] for entry in entries] == [
        "task_created",
        "tool_invocation_completed",
        "policy_rejection",
        "task_completed",
    ]


def test_read_task_log_returns_current_task_only_and_truncates(tmp_path) -> None:
    events = _store(tmp_path)
    events.append(
        kind=EventKind.TASK_CREATED,
        actor="runtime",
        task_id="other_task",
        payload={"goal": "ignore me"},
    )
    events.append(
        kind=EventKind.TASK_CREATED,
        actor="runtime",
        task_id="task_test",
        payload={"goal": "keep me"},
    )
    for idx in range(5):
        events.append(
            kind=EventKind.TOOL_INVOCATION_STARTED,
            actor="planner",
            task_id="task_test",
            step_id=f"step_{idx}",
            payload={"tool_name": "noop_observe", "reason": f"step {idx}"},
        )

    tool = ReadTaskLogTool(work_log=TaskWorkLog(events))
    result = tool.invoke(_read_log_inv(max_entries=3))

    assert result.status == "success"
    out = result.output
    assert out["task_id"] == "task_test"
    assert out["total_entries"] == 6
    assert out["returned_entries"] == 3
    assert out["truncated"] is True
    assert out["summary"]["goal"] == "keep me"
    assert all(entry.get("goal") != "ignore me" for entry in out["entries"])
    assert [entry["step_id"] for entry in out["entries"]] == [
        "step_2",
        "step_3",
        "step_4",
    ]


def test_work_log_summarises_current_discovery_output_shapes(tmp_path) -> None:
    events = _store(tmp_path)
    events.append(
        kind=EventKind.TOOL_INVOCATION_COMPLETED,
        actor="tool:find_files",
        task_id="task_test",
        payload={
            "tool_name": "find_files",
            "status": "success",
            "output_keys": ["count", "matches"],
            "output": {
                "count": 2,
                "matches": [
                    {"relative_path": "README.md", "size_bytes": 10},
                    {"relative_path": "docs/README.md", "size_bytes": 20},
                ],
            },
        },
    )
    events.append(
        kind=EventKind.TOOL_INVOCATION_COMPLETED,
        actor="tool:search_text",
        task_id="task_test",
        payload={
            "tool_name": "search_text",
            "status": "success",
            "output_keys": ["count", "matches"],
            "output": {
                "count": 1,
                "matches": [
                    {
                        "relative_path": "README.md",
                        "line_number": 12,
                        "line": "needle",
                    }
                ],
            },
        },
    )

    entries = TaskWorkLog(events).get_entries("task_test")

    assert entries[0]["output_summary"] == {
        "count": 2,
        "first_paths": ["README.md", "docs/README.md"],
    }
    assert entries[1]["output_summary"] == {
        "count": 1,
        "first_matches": [{"relative_path": "README.md", "line_number": 12}],
    }

