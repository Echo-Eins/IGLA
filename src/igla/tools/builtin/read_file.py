"""``read_file`` — workspace-bounded file reader with line-range support.

The tool reads a text file inside the configured workspace and emits a
``FileReadReceipt``.  For large files the caller MUST supply an explicit line
range (``start_line`` / ``end_line``); without a range the tool refuses files
longer than ``_LINE_LIMIT`` and returns a ``FILE_TOO_LARGE`` error with an
iterative-read hint so the model knows exactly how to proceed.

Path semantics:
* Relative paths are resolved against ``workspace_root``.
* Paths that escape the workspace are rejected (``PATH_OUTSIDE_WORKSPACE``).

Line numbering:
* ``start_line`` — 0-indexed, inclusive (default 0).
* ``end_line``   — exclusive end, like Python slicing (default: whole file).
* ``end_line`` beyond the last line is silently capped at ``total_lines``.
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

_LINE_LIMIT = 1000

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "start_line": {
            "type": "integer",
            "minimum": 0,
            "description": "First line to return (0-indexed, inclusive). Default: 0.",
        },
        "end_line": {
            "type": "integer",
            "minimum": 1,
            "description": (
                f"Last line to return (exclusive end, like Python slicing). "
                f"If omitted and the file has ≤{_LINE_LIMIT} lines the whole file is "
                f"returned. If omitted and the file is larger the call fails with "
                f"FILE_TOO_LARGE — use start_line/end_line to read iteratively in "
                f"chunks (e.g. 0/{_LINE_LIMIT}, then {_LINE_LIMIT}/{_LINE_LIMIT*2}, …)."
            ),
        },
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "path",
        "content",
        "start_line",
        "end_line",
        "total_lines",
        "end_of_file",
        "truncated",
        "sha256",
        "receipt_id",
    ],
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "start_line": {"type": "integer", "minimum": 0},
        "end_line": {"type": "integer", "minimum": 0},
        "total_lines": {"type": "integer", "minimum": 0},
        "end_of_file": {
            "type": "boolean",
            "description": "True when end_line reached the last line of the file.",
        },
        "truncated": {
            "type": "boolean",
            "description": "Always False — the tool never silently truncates.",
        },
        "sha256": {"type": "string"},
        "receipt_id": {"type": "string"},
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
                    "Read a workspace-bounded text file. "
                    "For files longer than 1000 lines supply start_line and end_line "
                    "to read iteratively; omitting the range on a large file returns "
                    "FILE_TOO_LARGE with the total line count and an iterative-read hint."
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
        start_line = int(invocation.input.get("start_line", 0))
        end_line_req = invocation.input.get("end_line")  # None = caller did not set

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
            raw = target.read_bytes()
        except OSError as exc:
            return _failure(
                invocation,
                code="READ_FAILED",
                message=f"failed to read {target}: {exc}",
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")

        all_lines = text.splitlines()
        total_lines = len(all_lines)

        # No range given on a large file → structured error with iterative hint
        if end_line_req is None and total_lines > _LINE_LIMIT:
            return _failure(
                invocation,
                code="FILE_TOO_LARGE",
                message=(
                    f"file has {total_lines} lines; limit is {_LINE_LIMIT} lines per "
                    f"call without an explicit range. Read iteratively: call with "
                    f"start_line=0 end_line={_LINE_LIMIT}, then "
                    f"start_line={_LINE_LIMIT} end_line={_LINE_LIMIT * 2}, and so on "
                    f"until end_of_file=true."
                ),
            )

        # Resolve the actual slice
        actual_start = max(0, min(start_line, total_lines))
        if end_line_req is None:
            actual_end = total_lines
        else:
            actual_end = max(actual_start, min(int(end_line_req), total_lines))

        selected = all_lines[actual_start:actual_end]
        content = "\n".join(selected) + ("\n" if selected else "")
        end_of_file = actual_end >= total_lines

        receipt = self._receipts.record_file_read(
            task_id=invocation.task_id,
            path=str(target),
            content=content,
            bytes_read=len(content.encode("utf-8")),
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
                "start_line": actual_start,
                "end_line": actual_end,
                "total_lines": total_lines,
                "end_of_file": end_of_file,
                "truncated": False,
                "sha256": receipt.sha256,
                "receipt_id": receipt.receipt_id,
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
            retryable=code not in {"PATH_OUTSIDE_WORKSPACE", "NOT_A_FILE"},
            requires_diagnosis=True,
        ),
    )
