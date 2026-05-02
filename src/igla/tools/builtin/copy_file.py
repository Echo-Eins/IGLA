"""``copy_file`` — workspace-bounded file copy/create helper.

The tool handles the common operation "copy this file to a new path, optionally
append text" without forcing the LLM to materialize the whole file in
``patch_file.new_content``. It is intentionally conservative:

* source and destination must stay inside the workspace;
* source must be an existing regular file;
* destination parent must already exist;
* destination is not overwritten unless ``overwrite=true``;
* overwrites create a rollback snapshot before replacing bytes.
"""
from __future__ import annotations

import contextlib
import hashlib
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


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_path", "destination_path"],
    "properties": {
        "source_path": {
            "type": "string",
            "minLength": 1,
            "description": "Workspace-relative or absolute source file path.",
        },
        "destination_path": {
            "type": "string",
            "minLength": 1,
            "description": "Workspace-relative or absolute destination file path.",
        },
        "append_text": {
            "type": "string",
            "description": "Text to append to the copied bytes, encoded as utf-8.",
        },
        "overwrite": {
            "type": "boolean",
            "description": (
                "If false (default), fail when destination exists. If true, "
                "snapshot the existing destination before replacing it."
            ),
        },
        "reason": {
            "type": "string",
            "description": "Free-form rationale recorded in the tool output.",
        },
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "source_path",
        "destination_path",
        "bytes_source",
        "bytes_written",
        "appended_bytes",
        "sha256_source",
        "sha256_after",
        "overwrote",
        "backup_artifact_id",
        "rollback_plan_id",
        "receipt_id",
    ],
    "properties": {
        "source_path": {"type": "string"},
        "destination_path": {"type": "string"},
        "bytes_source": {"type": "integer", "minimum": 0},
        "bytes_written": {"type": "integer", "minimum": 0},
        "appended_bytes": {"type": "integer", "minimum": 0},
        "sha256_source": {"type": "string"},
        "sha256_after": {"type": "string"},
        "overwrote": {"type": "boolean"},
        "backup_artifact_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "rollback_plan_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "receipt_id": {"type": "string"},
        "reason": {"type": "string"},
    },
}


class CopyFileTool(Tool):
    """Create a destination file from an existing source file."""

    def __init__(
        self,
        *,
        workspace_root: str,
        receipts: ReceiptManager,
        rollback: RollbackManager,
    ) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.copy_file",
                name="copy_file",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Copy one workspace file to another path, optionally appending "
                    "utf-8 text. Use this for creating a copy; do not synthesize "
                    "large file contents into patch_file.new_content."
                ),
                summary="Copy a workspace file to a new path, optionally appending text.",
                capabilities=["fs.write", "fs.copy", "core.copy_file"],
                risk_level="mutating",
                side_effects=True,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                produces=[ArtifactSpec(artifact_type="FileSnapshot", schema_pattern="1.x")],
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
        inp = invocation.input
        source = self._resolve(str(inp["source_path"]))
        destination = self._resolve(str(inp["destination_path"]))
        append_text = str(inp.get("append_text", ""))
        append_bytes = append_text.encode("utf-8")
        overwrite = bool(inp.get("overwrite", False))
        reason = str(inp.get("reason", "")) or None

        if source is None:
            return _failure(
                invocation,
                code="SOURCE_OUTSIDE_WORKSPACE",
                message=f"source path escapes workspace: {inp['source_path']}",
            )
        if destination is None:
            return _failure(
                invocation,
                code="DESTINATION_OUTSIDE_WORKSPACE",
                message=f"destination path escapes workspace: {inp['destination_path']}",
            )
        if source == destination:
            return _failure(
                invocation,
                code="SAME_PATH",
                message="source_path and destination_path resolve to the same file",
            )
        if not source.exists():
            return _failure(
                invocation,
                code="SOURCE_NOT_FOUND",
                message=f"source file not found: {source}",
            )
        if not source.is_file():
            return _failure(
                invocation,
                code="SOURCE_NOT_A_FILE",
                message=f"source is not a regular file: {source}",
            )
        if not destination.parent.exists():
            return _failure(
                invocation,
                code="DESTINATION_PARENT_NOT_FOUND",
                message=f"destination parent does not exist: {destination.parent}",
            )
        if not destination.parent.is_dir():
            return _failure(
                invocation,
                code="DESTINATION_PARENT_NOT_DIRECTORY",
                message=f"destination parent is not a directory: {destination.parent}",
            )

        destination_exists = destination.exists()
        if destination_exists and not destination.is_file():
            return _failure(
                invocation,
                code="DESTINATION_NOT_A_FILE",
                message=f"destination is not a regular file: {destination}",
            )
        if destination_exists and not overwrite:
            return _failure(
                invocation,
                code="DESTINATION_EXISTS",
                message=(
                    f"destination already exists: {destination}. Set overwrite=true "
                    "only if replacing it is intended."
                ),
            )

        try:
            source_bytes = source.read_bytes()
        except OSError as exc:
            return _failure(
                invocation,
                code="READ_FAILED",
                message=f"failed to read source {source}: {exc}",
                retryable=True,
            )

        backup_artifact_id: str | None = None
        rollback_plan_id: str | None = None
        if destination_exists:
            try:
                snapshot = self._rollback.snapshot(
                    path=destination,
                    task_id=invocation.task_id,
                    step_id=invocation.step_id,
                    producer_tool="copy_file",
                    producer_invocation_id=invocation.invocation_id,
                )
                plan = self._rollback.plan(
                    step_id=invocation.step_id or invocation.invocation_id,
                    snapshot_ids=[snapshot.snapshot_id],
                )
            except RollbackError as exc:
                return _failure(
                    invocation,
                    code="BACKUP_FAILED",
                    message=f"failed to back up destination before overwrite: {exc}",
                )
            backup_artifact_id = snapshot.snapshot_id
            rollback_plan_id = plan.plan_id

        written = source_bytes + append_bytes
        try:
            _atomic_write_bytes(destination, written)
        except OSError as exc:
            if backup_artifact_id:
                with contextlib.suppress(Exception):
                    self._rollback.restore_snapshot(backup_artifact_id)
            return _failure(
                invocation,
                code="WRITE_FAILED",
                message=f"failed to write destination {destination}: {exc}",
                retryable=True,
            )

        text_for_receipt = written.decode("utf-8", errors="replace")
        receipt = self._receipts.record_file_read(
            task_id=invocation.task_id,
            path=str(destination),
            content=text_for_receipt,
            bytes_read=len(written),
            step_id=invocation.step_id,
            file_sha256=_hash_bytes(written),
        )

        output: dict[str, Any] = {
            "source_path": str(source),
            "destination_path": str(destination),
            "bytes_source": len(source_bytes),
            "bytes_written": len(written),
            "appended_bytes": len(append_bytes),
            "sha256_source": _hash_bytes(source_bytes),
            "sha256_after": _hash_bytes(written),
            "overwrote": destination_exists,
            "backup_artifact_id": backup_artifact_id,
            "rollback_plan_id": rollback_plan_id,
            "receipt_id": receipt.receipt_id,
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

    def _resolve(self, raw_path: str) -> Path | None:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            return None
        return resolved


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    tmp = target.with_suffix(target.suffix + ".igla.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


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
            requires_diagnosis=code
            in {
                "SOURCE_NOT_FOUND",
                "DESTINATION_EXISTS",
                "WRITE_FAILED",
                "BACKUP_FAILED",
            },
        ),
    )
