"""``read_file`` — workspace-bounded file reader.

Even though the sandbox is not yet in place, we already enforce the basic
``allowed_paths`` semantics here: a tool MUST refuse paths that escape
``workspace_root``. This is the seed of the future ``path_allowed`` policy
predicate.
"""
from __future__ import annotations

from pathlib import Path

from ...kernel.receipt_manager import ReceiptManager
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
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "max_bytes": {"type": "integer", "minimum": 1, "maximum": 10_000_000},
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "content", "bytes_read", "sha256", "receipt_id"],
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "bytes_read": {"type": "integer", "minimum": 0},
        "sha256": {"type": "string"},
        "receipt_id": {"type": "string"},
        "truncated": {"type": "boolean"},
    },
}


class ReadFileTool(Tool):
    def __init__(self, *, workspace_root: str, receipts: ReceiptManager) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.read_file",
                name="read_file",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Read a workspace-bounded text file and emit a FileReadReceipt. "
                    "The path must be inside the configured workspace; relative paths "
                    "are resolved against the workspace root."
                ),
                capabilities=["fs.read", "core.read_file"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                produces=[ArtifactSpec(artifact_type="FileSnapshot", schema_pattern="1.x")],
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=30, max_file_size_mb=10),
                sandbox=SandboxSpec(profile="read_only_file_access"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()
        self._receipts = receipts

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        raw_path = str(invocation.input["path"])
        max_bytes = int(invocation.input.get("max_bytes", 1_000_000))

        target = self._resolve(raw_path)
        if target is None:
            return _failure(
                invocation,
                code="PATH_OUTSIDE_WORKSPACE",
                message=f"path escapes workspace: {raw_path}",
            )
        if not target.exists():
            return _failure(
                invocation,
                code="FILE_NOT_FOUND",
                message=f"file not found: {target}",
            )
        if not target.is_file():
            return _failure(
                invocation,
                code="NOT_A_FILE",
                message=f"not a regular file: {target}",
            )
        try:
            data = target.read_bytes()
        except OSError as exc:
            return _failure(
                invocation,
                code="READ_FAILED",
                message=f"failed to read {target}: {exc}",
            )

        truncated = False
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True

        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = data.decode("utf-8", errors="replace")

        receipt = self._receipts.record_file_read(
            task_id=invocation.task_id,
            path=str(target),
            content=content,
            bytes_read=len(data),
            step_id=invocation.step_id,
        )

        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={
                "path": str(target),
                "content": content,
                "bytes_read": len(data),
                "sha256": receipt.sha256,
                "receipt_id": receipt.receipt_id,
                "truncated": truncated,
            },
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


def _failure(invocation: ToolInvocation, *, code: str, message: str) -> ToolResult:
    return ToolResult(
        invocation_id=invocation.invocation_id,
        task_id=invocation.task_id,
        step_id=invocation.step_id,
        status="failed",
        error=ToolError(
            kind="FileSystemError",
            code=code,
            message=message,
            retryable=False,
            requires_diagnosis=True,
        ),
    )
