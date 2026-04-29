"""TaskSpec — the contract of one user request."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(str, Enum):
    INTAKE = "intake"
    READY = "ready"
    RUNNING = "running"
    NEEDS_USER_CLARIFICATION = "needs_user_clarification"
    NEEDS_USER_APPROVAL = "needs_user_approval"
    FAILURE_DIAGNOSIS_REQUIRED = "failure_diagnosis_required"
    BLOCKED = "blocked"
    DONE = "done"
    ABORTED = "aborted"


class TaskConstraints(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    no_file_mutation: bool = False
    no_network: bool = True
    max_runtime_minutes: int = 60
    max_iterations: int = 60
    max_clarification_depth: int = 4


class TaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    raw_request: str
    goal: str

    constraints: TaskConstraints = Field(default_factory=TaskConstraints)

    success_criteria: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)

    status: TaskStatus = TaskStatus.INTAKE
    created_at: datetime

    metadata: dict[str, Any] = Field(default_factory=dict)
