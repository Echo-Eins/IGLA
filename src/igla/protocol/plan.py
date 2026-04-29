"""Plan and PlanStep — DAG of typed steps."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .invocation import ToolRef


class StepStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"


class StepKind(str, Enum):
    TOOL_INVOCATION = "tool_invocation"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    TODO_BRANCH = "todo_branch"
    TODO_COMPLETE = "todo_complete"
    DECLARE_TASK_DONE = "declare_task_done"


class VerificationSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["schema_validation", "command", "http", "process_alive", "composite", "none"] = (
        "schema_validation"
    )
    artifact_type: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class OnFailureSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["enter_diagnosis", "abort", "skip"] = "enter_diagnosis"
    allowed_next_actions: list[str] = Field(default_factory=list)
    rollback_supported: bool = False


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    task_id: str
    todo_node_id: str | None = None

    kind: StepKind
    tool: ToolRef | None = None
    input: dict[str, Any] = Field(default_factory=dict)

    depends_on: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    expected_outputs: list[dict[str, Any]] = Field(default_factory=list)

    verification: VerificationSpec = Field(default_factory=VerificationSpec)
    on_failure: OnFailureSpec = Field(default_factory=OnFailureSpec)

    status: StepStatus = StepStatus.PROPOSED
    created_at: datetime
    updated_at: datetime
    reason: str | None = None
    invocation_id: str | None = None
    last_error_code: str | None = None


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    task_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: datetime
