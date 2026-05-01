"""``restore_file`` — explicit revert of a prior ``patch_file`` invocation.

Companion tool to ``patch_file``. The runtime can also restore via
``RollbackManager.execute(plan_id)``, but exposing a planner-callable tool
gives the model a way to undo a bad patch when verifier output makes that
the right move.

Lookup is by ``backup_artifact_id`` (the value emitted by ``patch_file``
in its output). The artifact's ``original_path`` metadata determines
where the bytes go; the planner cannot redirect the restore elsewhere
in this MVP — that would defeat the audit trail.

Failure modes:

* ``BACKUP_NOT_FOUND``        — artifact_id is unknown to ArtifactStore.
* ``BACKUP_NOT_RESTORABLE``   — artifact has no ``original_path`` metadata
                                (was not created by ``patch_file`` /
                                ``RollbackManager``).
* ``PATH_OUTSIDE_WORKSPACE``  — restored path is no longer within the
                                workspace (e.g. workspace was relocated).
* ``WRITE_FAILED``            — OS-level write error.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...kernel.receipt_manager import ReceiptManager
from ...kernel.rollback_manager import RollbackError, RollbackManager
from ...protocol.invocation import ToolInvocation
from ...protocol.manifest import (
    ArtifactSpec,
    PolicyRequirements,
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    VerifierSpec,
)
from ...protocol.result import ToolError, ToolResult
from ..base import Tool

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["backup_artifact_id"],
    "properties": {
        "backup_artifact_id": {
            "type": "string",
            "minLength": 1,
            "description": (
                "ArtifactStore id of the backup snapshot. Take this from "
                "patch_file's output.backup_artifact_id."
            ),
        },
        "reason": {
            "type": "string",
            "description": "Free-form rationale recorded with the restore event.",
        },
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "path",
        "backup_artifact_id",
        "sha256_after",
        "bytes_restored",
    ],
    "properties": {
        "path": {"type": "string"},
        "backup_artifact_id": {"type": "string"},
        "sha256_after": {"type": "string"},
        "bytes_restored": {"type": "integer", "minimum": 0},
    },
}


class RestoreFileTool(Tool):
    """Explicit rollback of a single patch_file backup."""

    def __init__(
        self,
        *,
        workspace_root: str,
        receipts: ReceiptManager,
        rollback: RollbackManager,
    ) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.restore_file",
                name="restore_file",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Restore a file from a patch_file backup. Pass the "
                    "backup_artifact_id from a prior patch_file output."
                ),
                summary="Revert a previous patch_file invocation.",
                capabilities=["fs.write", "fs.restore", "core.restore_file"],
                risk_level="mutating",
                side_effects=True,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                consumes=[ArtifactSpec(artifact_type="FileSnapshot", schema_pattern="1.x")],
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=30, max_file_size_mb=10),
                sandbox=SandboxSpec(profile="write_workspace"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()
        self._receipts = receipts
        self._rollback = rollback

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        backup_id = str(invocation.input["backup_artifact_id"])
        reason = str(invocation.input.get("reason", "")) or None

        # Look up the snapshot to know where it goes.
        try:
            snapshot = self._rollback.get_snapshot(backup_id)
        except RollbackError as exc:
            return _failure(
                invocation,
                code="BACKUP_NOT_RESTORABLE",
                message=str(exc),
            )
        except Exception as exc:  # ArtifactStoreError, etc.
            return _failure(
                invocation,
                code="BACKUP_NOT_FOUND",
                message=f"backup artifact not found: {backup_id} ({exc})",
            )

        target = Path(snapshot.original_path)
        try:
            target.resolve().relative_to(self._workspace)
        except ValueError:
            return _failure(
                invocation,
                code="PATH_OUTSIDE_WORKSPACE",
                message=(
                    f"backup original_path {target} is outside workspace "
                    f"{self._workspace}"
                ),
            )

        try:
            restored_path, sha256_after = self._rollback.restore_snapshot(backup_id)
        except OSError as exc:
            return _failure(
                invocation,
                code="WRITE_FAILED",
                message=f"failed to write {target}: {exc}",
                retryable=True,
            )
        except RollbackError as exc:
            return _failure(
                invocation,
                code="BACKUP_NOT_RESTORABLE",
                message=str(exc),
            )

        # Refresh receipt so subsequent patch_file calls see the restored hash.
        try:
            content_text = restored_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content_text = ""
        bytes_restored = restored_path.stat().st_size if restored_path.exists() else 0

        self._receipts.record_file_read(
            task_id=invocation.task_id,
            path=str(restored_path),
            content=content_text,
            bytes_read=bytes_restored,
            step_id=invocation.step_id,
            file_sha256=sha256_after,
        )

        output: dict[str, Any] = {
            "path": str(restored_path),
            "backup_artifact_id": backup_id,
            "sha256_after": sha256_after,
            "bytes_restored": bytes_restored,
        }
        if reason:
            output["reason"] = reason

        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output=output,
        )


def _failure(
    invocation: ToolInvocation,
    *,
    code: str,
    message: str,
    retryable: bool = False,
) -> ToolResult:
    return ToolResult(
        invocation_id=invocation.invocation_id,
        task_id=invocation.task_id,
        step_id=invocation.step_id,
        status="failed",
        error=ToolError(
            kind="FileSystemError",
            code=code,
            message=message,
            retryable=retryable,
            requires_diagnosis=code in {"WRITE_FAILED"},
        ),
    )
