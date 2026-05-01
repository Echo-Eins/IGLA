"""``read_task_log`` — let the model inspect its own task work history.

The tool surfaces a compact, structured journal of the planner's actions
in the *current* task (``invocation.task_id``).  It is not a general
audit endpoint: there is no way to ask for a different task — the
runtime decides which task is "current" and the model can only read it.

This is the first knock-on of the memory subsystem.  The journal
itself is computed by :class:`TaskWorkLog`, which is backed by the
EventStore.  Long-term/cross-task memory will arrive as a separate tool
when the structure is in place.

Output payload:

* ``task_id``        — task whose log was returned.
* ``goal``           — original goal text from ``task_created``.
* ``status``         — ``in_progress`` | ``done`` | ``failed``.
* ``closed``         — true once a ``task_completed``/``task_failed``
                        event was recorded; the log is "closed" for
                        memory purposes.
* ``total_entries``  — total number of work-relevant events on file.
* ``returned_entries`` — number of entries actually returned (may be
                        less than ``total_entries`` if ``max_entries``
                        truncated).
* ``truncated``      — convenience flag.
* ``entries``        — most-recent-last array of compact entries.
* ``summary``        — high-level rollup (counts, files touched).

There is no failure mode the model can trigger from a malformed query;
``max_entries`` is bounded by the schema.  Callers from outside the
planner must still pass a valid ``task_id`` via the invocation header
(this is the runtime's job, not the model's).
"""
from __future__ import annotations

from typing import Any

from ...kernel.task_work_log import TaskWorkLog
from ...protocol.invocation import ToolInvocation
from ...protocol.manifest import (
    PolicyRequirements,
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    VerifierSpec,
)
from ...protocol.result import ToolResult
from ..base import Tool

_DEFAULT_MAX_ENTRIES = 50

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_entries": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": (
                f"Maximum number of work-log entries to return (default "
                f"{_DEFAULT_MAX_ENTRIES}). When truncating, the most recent "
                f"entries are kept."
            ),
        },
        "reason": {
            "type": "string",
            "description": "Free-form rationale for the audit trail.",
        },
    },
}

_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["seq", "event_id", "kind", "actor", "timestamp"],
    "properties": {
        "seq": {"type": "integer", "minimum": 1},
        "event_id": {"type": "string"},
        "kind": {"type": "string"},
        "actor": {"type": "string"},
        "timestamp": {"type": "string"},
        "step_id": {"type": "string"},
    },
}

_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["task_id", "tool_calls", "tool_failures", "status", "closed"],
    "properties": {
        "task_id": {"type": "string"},
        "goal": {"type": ["string", "null"]},
        "tool_calls": {"type": "integer", "minimum": 0},
        "tool_failures": {"type": "integer", "minimum": 0},
        "policy_rejections": {"type": "integer", "minimum": 0},
        "last_tool": {"type": ["string", "null"]},
        "files_touched": {"type": "array", "items": {"type": "string"}},
        "closed": {"type": "boolean"},
        "status": {"type": "string", "enum": ["in_progress", "done", "failed"]},
        "summary": {"type": ["string", "null"]},
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "task_id",
        "closed",
        "total_entries",
        "returned_entries",
        "truncated",
        "entries",
        "summary",
    ],
    "properties": {
        "task_id": {"type": "string"},
        "closed": {"type": "boolean"},
        "total_entries": {"type": "integer", "minimum": 0},
        "returned_entries": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
        "entries": {"type": "array", "items": _ENTRY_SCHEMA},
        "summary": _SUMMARY_SCHEMA,
    },
}


class ReadTaskLogTool(Tool):
    """Per-task, read-only work-log accessor."""

    def __init__(self, *, work_log: TaskWorkLog) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.read_task_log",
                name="read_task_log",
                namespace="core.memory",
                version="1.0.0",
                description=(
                    "Return the model's own work history for the current task: "
                    "tool calls, results, policy rejections, user inputs, and "
                    "closure status. The tool always operates on the current "
                    "task_id; there is no way to query another task."
                ),
                summary="Read the current task's work journal.",
                capabilities=["memory.read", "core.read_task_log"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=10, max_file_size_mb=4),
                sandbox=SandboxSpec(profile="read_only_file_access"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._work_log = work_log

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        max_entries_in = invocation.input.get("max_entries", _DEFAULT_MAX_ENTRIES)
        max_entries = int(max_entries_in)

        # Total number of relevant events (no truncation).
        full_entries = self._work_log.get_entries(invocation.task_id)
        total = len(full_entries)
        if total > max_entries:
            entries = full_entries[-max_entries:]
            truncated = True
        else:
            entries = full_entries
            truncated = False

        summary = self._work_log.get_summary(invocation.task_id)
        closed = bool(summary.get("closed", False))

        output: dict[str, Any] = {
            "task_id": invocation.task_id,
            "closed": closed,
            "total_entries": total,
            "returned_entries": len(entries),
            "truncated": truncated,
            "entries": entries,
            "summary": summary,
        }

        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output=output,
        )
