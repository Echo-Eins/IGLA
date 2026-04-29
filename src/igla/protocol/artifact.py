"""Artifact descriptors and creation requests.

Artifacts are immutable, content-addressable units of data exchanged between
tools. Modules never pass raw payloads to each other — they reference an
``artifact_id`` and let the kernel resolve location/content/hash.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    """Pointer to an existing artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    artifact_type: str
    schema_version: str


class ArtifactDescriptor(BaseModel):
    """Full artifact metadata kept in ArtifactStore."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    artifact_type: str
    schema_version: str

    producer_tool: str | None = None
    producer_version: str | None = None
    producer_invocation_id: str | None = None

    location: str | None = None
    content_hash: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parents: list[str] = Field(default_factory=list)

    created_at: datetime


class ArtifactCreateRequest(BaseModel):
    """Request to create a new artifact in ArtifactStore.

    Either ``inline_content`` (small JSON payload) or ``location`` must be set,
    not both. Setting both is rejected by the store.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str
    schema_version: str

    inline_content: dict[str, Any] | None = None
    location: str | None = None

    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parents: list[str] = Field(default_factory=list)

    producer_tool: str | None = None
    producer_version: str | None = None
    producer_invocation_id: str | None = None
