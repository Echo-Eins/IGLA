"""``patch_file`` — workspace-bounded mutating file editor with rollback.

The single mutating tool of the MVP. Every successful invocation:

1. Verifies the caller has read the file (``read_before_write``).
2. Verifies the on-disk file still matches the read receipt
   (``hash_matches_receipt`` / ``hash_before_patch``).
3. Creates an immutable backup snapshot via ``RollbackManager`` BEFORE
   touching disk.
4. Applies the patch (full replacement OR exact ``search``→``replacement``).
5. Writes the new bytes atomically and re-hashes the result.
6. Updates the read receipt so a subsequent ``patch_file`` requires a
   fresh read.

Two patch modes are supported:

* **full_replace** — the caller supplies ``new_content``. Used for
  rewrites, code generation, or content-aware edits where the whole
  file is regenerated.

* **search_replace** — the caller supplies an exact ``search`` string
  and ``replacement``. Defaults to single-occurrence replacement; pass
  ``replace_all=true`` to replace every occurrence. Ambiguous matches
  (count > 1 with ``replace_all=false``) are rejected with
  ``AMBIGUOUS_SEARCH`` so the model is forced to be specific.

Failure modes (``status="failed"``):

* ``PATH_OUTSIDE_WORKSPACE``  — path escapes ``workspace_root``.
* ``FILE_NOT_FOUND``          — target does not exist.
* ``NOT_A_FILE``              — target is a directory or special file.
* ``CONFLICTING_PATCH_MODES`` — both ``new_content`` and ``search`` set.
* ``MISSING_PATCH_CONTENT``   — neither ``new_content`` nor ``search`` set.
* ``MISSING_REPLACEMENT``     — ``search`` set without ``replacement``.
* ``HASH_MISMATCH_FILE_CHANGED`` — file changed since ``read_file``.
* ``SEARCH_NOT_FOUND``        — exact ``search`` string is not in the file.
* ``AMBIGUOUS_SEARCH``        — ``search`` matches multiple times and
                                 ``replace_all`` is false.
* ``WRITE_FAILED``            — OS-level write error; backup is restored
                                 best-effort before returning.
* ``BACKUP_FAILED``           — RollbackManager refused to snapshot.

Output payload:

* ``backup_artifact_id``      — handle for ``restore_file``.
* ``rollback_plan_id``        — handle the runtime can pass to
                                 ``RollbackManager.execute(plan_id)``.
* ``sha256_before`` / ``sha256_after`` — full-file hashes before and after
                                 the mutation; ``sha256_after`` is the
                                 value to pass as ``base_sha256`` for any
                                 follow-up ``patch_file`` of this file.
* ``patch_mode``              — ``"full_replace" | "search_replace"``.
* ``occurrences_replaced``    — for ``search_replace`` mode.
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
    "required": ["path", "base_sha256"],
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "description": "Workspace-relative or absolute path to patch.",
        },
        "base_sha256": {
            "type": "string",
            "minLength": 1,
            "description": (
                "The file_sha256 value from a prior read_file call. "
                "Required to enforce read-before-write integrity. "
                "If the file has changed since the read, the patch is rejected."
            ),
        },
        "new_content": {
            "type": "string",
            "description": (
                "Complete replacement for the file. Mutually exclusive with "
                "search/replacement."
            ),
        },
        "search": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact substring to find and replace. Use together with "
                "replacement. By default a unique match is required."
            ),
        },
        "replacement": {
            "type": "string",
            "description": "Replacement string for the search substring.",
        },
        "replace_all": {
            "type": "boolean",
            "description": (
                "If true, replace every occurrence of search. If false (default), "
                "exactly one occurrence must exist or the patch is rejected with "
                "AMBIGUOUS_SEARCH."
            ),
        },
        "encoding": {
            "type": "string",
            "description": "File encoding (default: utf-8).",
        },
        "reason": {
            "type": "string",
            "description": (
                "Free-form rationale recorded as evidence with the backup artifact."
            ),
        },
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "path",
        "backup_artifact_id",
        "rollback_plan_id",
        "sha256_before",
        "sha256_after",
        "bytes_before",
        "bytes_after",
        "lines_before",
        "lines_after",
        "patch_mode",
        "occurrences_replaced",
    ],
    "properties": {
        "path": {"type": "string"},
        "backup_artifact_id": {
            "type": "string",
            "description": (
                "Pre-mutation backup. Pass to restore_file to revert this patch."
            ),
        },
        "rollback_plan_id": {
            "type": "string",
            "description": "RollbackManager plan id for runtime-driven rollback.",
        },
        "sha256_before": {"type": "string"},
        "sha256_after": {
            "type": "string",
            "description": (
                "Full-file SHA-256 after the patch. Use this value as base_sha256 "
                "for any follow-up patch_file of the same file."
            ),
        },
        "bytes_before": {"type": "integer", "minimum": 0},
        "bytes_after": {"type": "integer", "minimum": 0},
        "lines_before": {"type": "integer", "minimum": 0},
        "lines_after": {"type": "integer", "minimum": 0},
        "patch_mode": {
            "type": "string",
            "enum": ["full_replace", "search_replace"],
        },
        "occurrences_replaced": {"type": "integer", "minimum": 0},
    },
}


class PatchFileTool(Tool):
    """Mutating file editor with mandatory backup."""

    def __init__(
        self,
        *,
        workspace_root: str,
        receipts: ReceiptManager,
        rollback: RollbackManager,
    ) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.patch_file",
                name="patch_file",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Edit a workspace file under the read-before-write + "
                    "hash-before-patch + backup-before-mutation invariants. "
                    "Requires base_sha256 from a prior read_file call. "
                    "Either new_content (full replace) OR search+replacement."
                ),
                summary="Mutate a workspace file with mandatory backup.",
                capabilities=["fs.write", "fs.patch", "core.patch_file"],
                risk_level="mutating",
                side_effects=True,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                consumes=[ArtifactSpec(artifact_type="FileSnapshot", schema_pattern="1.x")],
                produces=[ArtifactSpec(artifact_type="FileSnapshot", schema_pattern="1.x")],
                policies=PolicyRequirements(
                    requires=[
                        "read_before_write",
                        "hash_matches_receipt",
                        "backup_before_mutation",
                    ]
                ),
                resources=ResourceLimits(timeout_seconds=30, max_file_size_mb=10),
                sandbox=SandboxSpec(profile="write_workspace"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()
        self._receipts = receipts
        self._rollback = rollback

    # ------------------------------------------------------------------ #

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        inp = invocation.input
        raw_path = str(inp["path"])
        base_sha256 = str(inp["base_sha256"])
        new_content = inp.get("new_content")
        search = inp.get("search")
        replacement = inp.get("replacement")
        replace_all = bool(inp.get("replace_all", False))
        encoding = str(inp.get("encoding", "utf-8"))
        reason = str(inp.get("reason", "")) or None

        # ---- Path resolution ---------------------------------------------------
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
                message=f"file not found: {raw_path}",
            )
        if not target.is_file():
            return _failure(
                invocation,
                code="NOT_A_FILE",
                message=f"not a regular file: {raw_path}",
            )

        # ---- Patch-mode validation --------------------------------------------
        has_new = new_content is not None
        has_search = search is not None
        if has_new and has_search:
            return _failure(
                invocation,
                code="CONFLICTING_PATCH_MODES",
                message="provide either new_content OR search+replacement, not both",
            )
        if not has_new and not has_search:
            return _failure(
                invocation,
                code="MISSING_PATCH_CONTENT",
                message="provide either new_content OR search+replacement",
            )
        if has_search and replacement is None:
            return _failure(
                invocation,
                code="MISSING_REPLACEMENT",
                message="search requires a replacement string",
            )

        # ---- Read current bytes + verify hash ---------------------------------
        try:
            raw_before = target.read_bytes()
        except OSError as exc:
            return _failure(
                invocation,
                code="READ_FAILED",
                message=f"failed to read {raw_path}: {exc}",
            )
        sha256_before = _hash_bytes(raw_before)
        if sha256_before != base_sha256:
            return _failure(
                invocation,
                code="HASH_MISMATCH_FILE_CHANGED",
                message=(
                    f"file '{raw_path}' has changed since last read: "
                    f"expected {base_sha256}, got {sha256_before}. "
                    f"Re-read the file with read_file and retry with the new file_sha256."
                ),
            )

        try:
            text_before = raw_before.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return _failure(
                invocation,
                code="DECODE_FAILED",
                message=f"failed to decode {raw_path} as {encoding}",
            )

        # ---- Apply the patch in memory ----------------------------------------
        try:
            patched_text, patch_mode, occurrences = _apply_patch(
                original=text_before,
                new_content=new_content if has_new else None,
                search=str(search) if has_search else None,
                replacement=str(replacement) if replacement is not None else None,
                replace_all=replace_all,
            )
        except _PatchError as exc:
            return _failure(invocation, code=exc.code, message=exc.message)

        # No-op patch: same content. Don't bother creating a backup or writing.
        try:
            patched_bytes = patched_text.encode(encoding)
        except (UnicodeEncodeError, LookupError) as exc:
            return _failure(
                invocation,
                code="ENCODE_FAILED",
                message=f"failed to encode patched content as {encoding}: {exc}",
            )

        # ---- Snapshot BEFORE writing ------------------------------------------
        try:
            snapshot = self._rollback.snapshot(
                path=target,
                task_id=invocation.task_id,
                step_id=invocation.step_id,
                producer_tool="patch_file",
                producer_invocation_id=invocation.invocation_id,
            )
        except RollbackError as exc:
            return _failure(
                invocation,
                code="BACKUP_FAILED",
                message=f"failed to back up file before mutation: {exc}",
            )

        plan = self._rollback.plan(
            step_id=invocation.step_id or invocation.invocation_id,
            snapshot_ids=[snapshot.snapshot_id],
        )

        # ---- Write atomically -------------------------------------------------
        try:
            _atomic_write_bytes(target, patched_bytes)
        except OSError as exc:
            # Best-effort restore from the snapshot we just made.
            with contextlib.suppress(Exception):
                self._rollback.restore_snapshot(snapshot.snapshot_id)
            return _failure(
                invocation,
                code="WRITE_FAILED",
                message=f"failed to write {raw_path}: {exc}; backup restored",
                retryable=True,
            )

        sha256_after = _hash_bytes(patched_bytes)

        # ---- Refresh the read receipt so subsequent patches re-read -----------
        self._receipts.record_file_read(
            task_id=invocation.task_id,
            path=str(target),
            content=patched_text,
            bytes_read=len(patched_bytes),
            step_id=invocation.step_id,
            file_sha256=sha256_after,
        )

        output: dict[str, Any] = {
            "path": str(target),
            "backup_artifact_id": snapshot.snapshot_id,
            "rollback_plan_id": plan.plan_id,
            "sha256_before": sha256_before,
            "sha256_after": sha256_after,
            "bytes_before": len(raw_before),
            "bytes_after": len(patched_bytes),
            "lines_before": len(text_before.splitlines()),
            "lines_after": len(patched_text.splitlines()),
            "patch_mode": patch_mode,
            "occurrences_replaced": occurrences,
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

    # ------------------------------------------------------------------ #

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


# ---------------------------------------------------------------------------#
# Internal helpers                                                          #
# ---------------------------------------------------------------------------#


class _PatchError(Exception):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _apply_patch(
    *,
    original: str,
    new_content: str | None,
    search: str | None,
    replacement: str | None,
    replace_all: bool,
) -> tuple[str, str, int]:
    """Apply an in-memory patch.

    Returns ``(patched_text, patch_mode, occurrences_replaced)``.
    Raises ``_PatchError`` for caller-visible failures.
    """
    if new_content is not None:
        return new_content, "full_replace", 1

    assert search is not None  # patch-mode validation already enforced
    assert replacement is not None

    count = original.count(search)
    if count == 0:
        return _raise(
            "SEARCH_NOT_FOUND",
            "search string not found in file; verify the exact text including whitespace",
        )
    if count > 1 and not replace_all:
        return _raise(
            "AMBIGUOUS_SEARCH",
            (
                f"search string occurs {count} times. Either make it more specific "
                f"(include more surrounding context) or set replace_all=true."
            ),
        )
    if replace_all:
        patched = original.replace(search, replacement)
        return patched, "search_replace", count
    patched = original.replace(search, replacement, 1)
    return patched, "search_replace", 1


def _raise(code: str, message: str) -> tuple[str, str, int]:
    raise _PatchError(code=code, message=message)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write atomically by going through a temp file in the same directory."""
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
    non_retryable = {
        "PATH_OUTSIDE_WORKSPACE",
        "NOT_A_FILE",
        "CONFLICTING_PATCH_MODES",
        "MISSING_PATCH_CONTENT",
        "MISSING_REPLACEMENT",
        "DECODE_FAILED",
        "ENCODE_FAILED",
    }
    actual_retryable = retryable and code not in non_retryable
    return ToolResult(
        invocation_id=invocation.invocation_id,
        task_id=invocation.task_id,
        step_id=invocation.step_id,
        status="failed",
        error=ToolError(
            kind="FileSystemError",
            code=code,
            message=message,
            retryable=actual_retryable,
            requires_diagnosis=code in {
                "HASH_MISMATCH_FILE_CHANGED",
                "FILE_NOT_FOUND",
                "WRITE_FAILED",
                "BACKUP_FAILED",
            },
        ),
    )
