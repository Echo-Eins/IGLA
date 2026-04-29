"""Evidence model.

Every claim made by IGLA in a final report references one or more
``EvidenceRecord``. Evidence is created by tools (or by the kernel from tool
outputs) and is the only currency the verifier and report builder accept.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceKind(str, Enum):
    COMMAND_OUTPUT = "command_output"
    FILE_EXCERPT = "file_excerpt"
    LOG_EXCERPT = "log_excerpt"
    ARTIFACT_METADATA = "artifact_metadata"
    MEMORY_LOOKUP = "memory_lookup"
    DOCUMENTATION = "documentation"
    VERIFIER_OUTPUT = "verifier_output"
    USER_ATTESTATION = "user_attestation"
    EXTERNAL = "external"
    TOOL_OUTPUT = "tool_output"


class EvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: EvidenceKind
    summary: str | None = None


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str
    kind: EvidenceKind

    task_id: str
    step_id: str | None = None
    source_event_id: str | None = None
    artifact_id: str | None = None

    summary: str
    location: str | None = None
    content_hash: str | None = None
    confidence: str = "observed"  # observed | verified | inferred | external

    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class EvidenceCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EvidenceKind
    task_id: str
    step_id: str | None = None
    source_event_id: str | None = None
    artifact_id: str | None = None

    summary: str
    location: str | None = None
    content_hash: str | None = None
    confidence: str = "observed"

    metadata: dict[str, Any] = Field(default_factory=dict)
