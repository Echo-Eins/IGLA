"""Policy decision objects."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyDecisionKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_DIAGNOSIS = "needs_diagnosis"
    NEEDS_MORE_CONTEXT = "needs_more_context"


ActionKind = Literal[
    "tool_invocation",
    "ask_user_clarification",
    "todo_branch",
    "todo_complete",
    "declare_task_done",
]


class ActionRequest(BaseModel):
    """The thing being checked. Whatever the planner proposes is converted to
    an ActionRequest before policy evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ActionKind
    actor: str  # "planner" | "user" | "runtime"
    task_id: str
    step_id: str | None = None
    todo_node_id: str | None = None

    tool_name: str | None = None
    tool_version: str | None = None

    input: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class PolicyRejection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason_code: str
    message: str
    rule_id: str | None = None
    missing_requirements: list[str] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: PolicyDecisionKind
    action: ActionRequest

    rejection: PolicyRejection | None = None
    allowed_next_actions: list[str] = Field(default_factory=list)
    forbidden_next_actions: list[str] = Field(default_factory=list)

    needs_human_approval: bool = False
    rationale: str | None = None

    @property
    def is_allow(self) -> bool:
        return self.decision is PolicyDecisionKind.ALLOW

    @property
    def is_deny(self) -> bool:
        return self.decision is PolicyDecisionKind.DENY
