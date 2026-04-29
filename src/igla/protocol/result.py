"""ToolResult envelope and ToolError.

The shape mirrors ``ToolInvocation``. Tools return exactly one ToolResult
even on failure — they never raise out of ``invoke``. Raising is treated by
the executor as a kernel-level fault and results in a synthetic
``ToolResult(status=failed, error.kind=ToolException)``.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifact import ArtifactDescriptor
from .evidence import EvidenceRef


ToolResultStatus = Literal["success", "failed", "blocked", "partial"]


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str  # "ValidationError" | "ToolException" | "RuntimeError" | tool-specific
    code: str  # short SCREAMING_SNAKE identifier
    message: str
    retryable: bool = False
    requires_diagnosis: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResultMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_ms: int | None = None
    memory_peak_mb: float | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    protocol_version: str = "1.0"
    invocation_id: str
    task_id: str
    step_id: str | None = None

    status: ToolResultStatus

    output: dict[str, Any] = Field(default_factory=dict)

    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    metrics: ToolResultMetric = Field(default_factory=ToolResultMetric)

    error: ToolError | None = None
    next_hints: list[dict[str, Any]] = Field(default_factory=list)
