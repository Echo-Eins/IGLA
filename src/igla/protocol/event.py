"""Event records — the append-only audit log.

Events are the primary substrate from which memory, reports and debugging
output are derived. Events are immutable and never compacted in place.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventKind(str, Enum):
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_ARCHIVED = "task_archived"

    TODO_CREATED = "todo_created"
    TODO_BRANCHED = "todo_branched"
    TODO_UPDATED = "todo_updated"
    TODO_COMPLETED = "todo_completed"

    PLAN_PROPOSED = "plan_proposed"
    PLAN_APPROVED = "plan_approved"

    STEP_PROPOSED = "step_proposed"
    STEP_APPROVED = "step_approved"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_BLOCKED = "step_blocked"
    STEP_ROLLED_BACK = "step_rolled_back"
    STEP_SKIPPED = "step_skipped"

    LLM_PROPOSAL_RECEIVED = "llm_proposal_received"
    LLM_REQUEST_SENT = "llm_request_sent"

    POLICY_DECISION = "policy_decision"
    POLICY_REJECTION = "policy_rejection"

    TOOL_INVOCATION_STARTED = "tool_invocation_started"
    TOOL_INVOCATION_COMPLETED = "tool_invocation_completed"
    TOOL_INVOCATION_FAILED = "tool_invocation_failed"

    ARTIFACT_CREATED = "artifact_created"
    EVIDENCE_CREATED = "evidence_created"
    RECEIPT_CREATED = "receipt_created"

    RUNTIME_MODE_CHANGED = "runtime_mode_changed"
    USER_INPUT_RECEIVED = "user_input_received"
    USER_OUTPUT_SENT = "user_output_sent"
    SYSTEM_MESSAGE = "system_message"


class EventRecord(BaseModel):
    """One immutable line in the event journal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    task_id: str | None = None
    step_id: str | None = None

    kind: EventKind
    actor: str  # "runtime" | "planner" | "user" | "tool:<name>" | "policy"

    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
