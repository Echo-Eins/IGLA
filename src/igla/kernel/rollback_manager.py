"""Rollback manager — pre-mutation snapshots, plan execution, restore.

Design:

* **Storage delegated to ArtifactStore.** Every snapshot is an immutable
  artifact of type ``FileSnapshot``. ``ArtifactStore`` already provides
  content-addressing (SHA-256), persistent on-disk storage, and parent
  lineage, so the rollback manager only owns the *plan* layer on top.
* **Plans are in-process.** A ``RollbackPlan`` is a step-scoped declaration
  living in a thread-safe dict. Backup artifacts persist across restarts;
  the plan map does not (MVP).
* **Idempotent restore.** ``execute`` reads each snapshot's
  ``original_path`` from the artifact metadata and writes the bytes back
  exactly once per snapshot. ``restore_snapshot`` exposes the same
  primitive for the explicit ``restore_file`` tool.

Invariants:

* ``snapshot()`` MUST be called BEFORE any mutating tool touches the file.
  The patch tool is responsible for the ordering: read receipt → snapshot
  → write → verify.
* ``execute()`` is the only path that writes original_path back from a
  backup artifact. A failed execute returns a ``RollbackResult`` with
  ``success=False`` and ``failed_snapshot_ids`` populated; it does NOT
  raise.
* Backup artifacts are never deleted automatically. They remain in
  ArtifactStore as audit evidence.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..ids import prefixed_id
from ..protocol.artifact import ArtifactCreateRequest
from ..protocol.rollback import (
    RollbackPlan,
    RollbackResult,
    RollbackStrategy,
    SnapshotDescriptor,
)
from .artifact_store import ArtifactStore
from .clock import Clock
from .errors import KernelError


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class RollbackError(KernelError):
    """Raised when the rollback machinery is invoked with an invalid handle."""


class RollbackManager:
    """Owner of pre-mutation snapshots and rollback plans."""

    def __init__(self, artifacts: ArtifactStore, clock: Clock) -> None:
        self._artifacts = artifacts
        self._clock = clock
        self._plans: dict[str, RollbackPlan] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Snapshot creation                                                 #
    # ------------------------------------------------------------------ #

    def snapshot(
        self,
        *,
        path: Path,
        task_id: str,
        step_id: str | None = None,
        producer_tool: str | None = None,
        producer_invocation_id: str | None = None,
    ) -> SnapshotDescriptor:
        """Capture an immutable copy of ``path`` before mutation.

        ``path`` MUST be an absolute, already workspace-checked path. The
        rollback manager does NOT validate workspace bounds — that is the
        caller's responsibility (the patch tool resolves the path against
        the workspace before calling here).
        """
        if not path.exists():
            raise RollbackError(f"snapshot target does not exist: {path}")
        if not path.is_file():
            raise RollbackError(f"snapshot target is not a regular file: {path}")

        bytes_size = path.stat().st_size

        descriptor = self._artifacts.put(
            ArtifactCreateRequest(
                artifact_type="FileSnapshot",
                schema_version="1.0",
                location=str(path),
                summary=f"Pre-mutation backup of {path.name}",
                metadata={
                    "original_path": str(path),
                    "task_id": task_id,
                    "step_id": step_id or "",
                    "bytes_size": bytes_size,
                },
                producer_tool=producer_tool or "rollback_manager",
                producer_version="1.0.0",
                producer_invocation_id=producer_invocation_id,
            )
        )
        return SnapshotDescriptor(
            snapshot_id=descriptor.artifact_id,
            artifact_id=descriptor.artifact_id,
            original_path=str(path),
            file_sha256=descriptor.content_hash or "",
            bytes_size=bytes_size,
            task_id=task_id,
            step_id=step_id,
            created_at=descriptor.created_at,
        )

    def get_snapshot(self, snapshot_id: str) -> SnapshotDescriptor:
        """Re-hydrate a SnapshotDescriptor from ArtifactStore."""
        descriptor = self._artifacts.get(snapshot_id)
        meta = descriptor.metadata or {}
        original_path = str(meta.get("original_path", ""))
        if not original_path:
            raise RollbackError(
                f"snapshot {snapshot_id} has no original_path metadata; "
                f"likely not produced by rollback_manager"
            )
        return SnapshotDescriptor(
            snapshot_id=descriptor.artifact_id,
            artifact_id=descriptor.artifact_id,
            original_path=original_path,
            file_sha256=descriptor.content_hash or "",
            bytes_size=int(meta.get("bytes_size", 0)),
            task_id=str(meta.get("task_id", "")),
            step_id=(str(meta.get("step_id")) or None) if meta.get("step_id") else None,
            created_at=descriptor.created_at,
        )

    # ------------------------------------------------------------------ #
    #  Plan registration                                                 #
    # ------------------------------------------------------------------ #

    def plan(
        self,
        *,
        step_id: str,
        snapshot_ids: list[str],
        strategy: RollbackStrategy = "restore_backup",
        post_actions: list[str] | None = None,
        auto_timeout_seconds: int = 0,
    ) -> RollbackPlan:
        """Register a rollback plan for a step. Returns the frozen plan."""
        plan_id = prefixed_id("rbp")
        plan = RollbackPlan(
            plan_id=plan_id,
            step_id=step_id,
            strategy=strategy,
            snapshot_ids=list(snapshot_ids),
            post_actions=list(post_actions or []),
            auto_timeout_seconds=auto_timeout_seconds,
            created_at=self._clock.now(),
        )
        with self._lock:
            self._plans[plan_id] = plan
        return plan

    def get_plan(self, plan_id: str) -> RollbackPlan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def list_plans_for_step(self, step_id: str) -> list[RollbackPlan]:
        with self._lock:
            return [p for p in self._plans.values() if p.step_id == step_id]

    # ------------------------------------------------------------------ #
    #  Execution                                                         #
    # ------------------------------------------------------------------ #

    def execute(self, plan_id: str) -> RollbackResult:
        """Restore every snapshot in the plan.

        Best-effort across snapshots: a single failed restore does not
        prevent the rest from running. Returns a structured result with
        the list of restored paths and any failed snapshot ids.
        """
        with self._lock:
            plan = self._plans.get(plan_id)
        if plan is None:
            return RollbackResult(
                plan_id=plan_id,
                success=False,
                error="ROLLBACK_PLAN_NOT_FOUND",
                restored_paths=[],
                failed_snapshot_ids=[],
            )

        if plan.strategy != "restore_backup":
            return RollbackResult(
                plan_id=plan_id,
                success=False,
                error=f"strategy {plan.strategy!r} not implemented in MVP",
                restored_paths=[],
                failed_snapshot_ids=list(plan.snapshot_ids),
            )

        restored: list[str] = []
        failed: list[str] = []
        errors: list[str] = []

        for snapshot_id in plan.snapshot_ids:
            try:
                restored_path, _ = self.restore_snapshot(snapshot_id)
                restored.append(str(restored_path))
            except Exception as exc:  # noqa: BLE001 - rollback never raises out
                failed.append(snapshot_id)
                errors.append(f"{snapshot_id}: {exc}")

        return RollbackResult(
            plan_id=plan_id,
            success=not failed,
            error="; ".join(errors) if errors else None,
            restored_paths=restored,
            failed_snapshot_ids=failed,
        )

    def restore_snapshot(self, snapshot_id: str) -> tuple[Path, str]:
        """Restore a single snapshot. Returns (target_path, sha256_after).

        Raises RollbackError if the artifact is missing or has no
        ``original_path`` metadata. Used both by ``execute`` and by the
        explicit ``restore_file`` tool.
        """
        descriptor = self._artifacts.get(snapshot_id)
        meta = descriptor.metadata or {}
        original_raw = meta.get("original_path")
        if not original_raw:
            raise RollbackError(
                f"snapshot {snapshot_id} has no original_path metadata"
            )
        original_path = Path(str(original_raw))
        data = self._artifacts.open_bytes(snapshot_id)
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_bytes(data)
        return original_path, _hash_bytes(data)

    def discard(self, plan_id: str) -> None:
        """Drop the plan entry. Backup artifacts remain in ArtifactStore."""
        with self._lock:
            self._plans.pop(plan_id, None)
