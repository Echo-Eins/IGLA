"""ToolInvocation envelope.

A ToolInvocation is the **only** way a tool is ever called. Tools never
receive raw arguments or kernel handles; they receive a structured envelope.
Extensions to behavior live in the JSON body, never in the function signature.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str


class InvocationContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace_id: str = "default"
    working_dir: str | None = None
    artifact_scope: Literal["task", "session", "global"] = "task"
    mode: Literal["normal", "dry_run"] = "normal"
    extra: dict[str, Any] = Field(default_factory=dict)


class InvocationPolicyHints(BaseModel):
    """Hints about the policy envelope under which the tool will run.

    The Policy Engine is authoritative; values here are advisory and let
    tools fail fast when their declared limits are about to be violated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_level: Literal[
        "read_only", "create_only", "mutating", "execution", "system", "privileged"
    ] = "read_only"
    allowed_paths: list[str] = Field(default_factory=list)
    network: Literal["disabled", "declared_only", "full"] = "disabled"
    requires_approval: bool = False
    max_runtime_seconds: int = 120
    max_memory_mb: int = 4096


class InvocationDependencies(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    required_receipts: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)


class InvocationProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_by: str = "planner"
    reason: str | None = None
    parent_step_id: str | None = None
    todo_node_id: str | None = None


class ToolInvocation(BaseModel):
    """The single envelope a tool receives."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: str = "1.0"
    invocation_id: str
    task_id: str
    step_id: str | None = None

    tool: ToolRef
    input: dict[str, Any] = Field(default_factory=dict)

    context: InvocationContext = Field(default_factory=InvocationContext)
    policy: InvocationPolicyHints = Field(default_factory=InvocationPolicyHints)
    dependencies: InvocationDependencies = Field(default_factory=InvocationDependencies)
    provenance: InvocationProvenance = Field(default_factory=InvocationProvenance)
    extensions: dict[str, Any] = Field(default_factory=dict)
