"""Runtime modes and a pure read-only snapshot of runtime state.

The actual mutable runtime state lives in the kernel; ``RuntimeStateSnapshot``
is what gets serialised into prompts and policy decisions.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuntimeMode(str, Enum):
    READY = "ready"
    WAITING_FOR_TOOL_RESULT = "waiting_for_tool_result"
    NEEDS_USER_CLARIFICATION = "needs_user_clarification"
    NEEDS_USER_APPROVAL = "needs_user_approval"
    FAILURE_DIAGNOSIS_REQUIRED = "failure_diagnosis_required"
    BLOCKED = "blocked"
    TASK_DONE = "task_done"


class RuntimeStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    mode: RuntimeMode

    iteration: int
    consecutive_rejections: int

    last_event_id: str | None = None
    last_step_id: str | None = None
    last_error_code: str | None = None
    last_rejection_reason_code: str | None = None
    last_rejection_message: str | None = None

    allowed_next_actions: list[str] = Field(default_factory=list)
    forbidden_next_actions: list[str] = Field(default_factory=list)

    failure_classified: bool = False
    changed_condition_declared: bool = False

    captured_at: datetime
    extra: dict[str, Any] = Field(default_factory=dict)
