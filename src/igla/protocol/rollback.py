"""Rollback protocol envelopes — snapshots, plans, results.

These objects describe the *contract* of the rollback machinery, not its
implementation. ``RollbackManager`` (in ``kernel/``) owns the state and
side-effects; here we just freeze the shapes of what it produces and
consumes so policy/planner/tools can talk about rollback without leaking
implementation details.

Design choices:
* A ``SnapshotDescriptor`` is a lightweight pointer; the actual file bytes
  live as an immutable artifact in ``ArtifactStore``. This lets us reuse
  content-hashing, parent lineage, and on-disk persistence for free.
* A ``RollbackPlan`` is an in-process, step-scoped declaration. It is not
  persisted across restarts in MVP; if a process crashes mid-mutation,
  the backup artifact remains and can be restored manually via
  ``restore_file``. Persistence comes later, when the planner/runtime
  gains durable session continuation.
* ``RollbackResult`` is reported back to the planner so it sees that the
  pre-mutation state was restored — a critical bit of evidence when the
  failure-diagnosis loop kicks in.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RollbackStrategy = Literal[
    "restore_backup",
    "git_revert",
    "docker_recreate",
    "systemd_rollback",
]


class SnapshotDescriptor(BaseModel):
    """Pre-mutation snapshot of a file (or other resource).

    The snapshot is stored as an immutable artifact (``artifact_id`` ==
    ``snapshot_id``). The original path and pre-mutation hash are kept
    here so the rollback executor can restore deterministically.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    artifact_id: str  # ArtifactStore reference, equals snapshot_id by convention
    original_path: str  # absolute path of the snapshotted file
    file_sha256: str  # SHA-256 of the file at snapshot time
    bytes_size: int  # size of the snapshotted file in bytes

    task_id: str
    step_id: str | None = None
    created_at: datetime


class RollbackPlan(BaseModel):
    """A declared rollback strategy for one mutating step.

    The plan is created BEFORE the mutation runs and discarded if the
    verifier passes. If the verifier fails or the tool reports an error
    after the mutation has touched disk, ``RollbackManager.execute(plan_id)``
    restores every snapshot listed here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    step_id: str
    strategy: RollbackStrategy = "restore_backup"

    snapshot_ids: list[str] = Field(default_factory=list)
    post_actions: list[str] = Field(default_factory=list)

    auto_timeout_seconds: int = 0  # 0 = no auto-rollback
    created_at: datetime


class RollbackResult(BaseModel):
    """Outcome of a ``RollbackManager.execute(plan_id)`` call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    success: bool
    error: str | None = None
    restored_paths: list[str] = Field(default_factory=list)
    failed_snapshot_ids: list[str] = Field(default_factory=list)
