"""``verify_file`` — receipt-gated workspace verifier.

The verifier runs *only* on files that the model has already read or
patched in the current task (i.e. the receipt manager has a
``FileReadReceipt`` for the resolved path).  This guarantees the planner
cannot use the verifier to probe arbitrary parts of the workspace; it
can only validate its own work.

Verification is **not** automatic and **does not** trigger rollback.  The
planner calls ``verify_file`` explicitly after a mutation, reads the
structured result, and decides what to do next:

* keep the patch and continue;
* call ``patch_file`` again to fix a localised problem;
* call ``restore_file`` to revert if the patch is broken beyond repair.

Supported checks (auto-detected from file extension when ``checks`` is
omitted):

* ``syntax`` — pure-Python parse: ``ast.parse`` for ``.py``,
  ``json.loads`` for ``.json``, ``yaml.safe_load`` for ``.yaml``/``.yml``.
* ``lint`` — ``ruff check <path>`` (Python only).  Skipped silently with
  ``passed=True`` and ``skipped=True`` for non-Python files.
* ``pytest`` — ``python -m pytest <test_path>`` against tests inside the
  workspace.  Always opt-in: requires either an explicit ``test_path``
  or an explicit ``checks=["pytest"]``.

Auto-detection only ever runs the cheap, file-local checks (``syntax``,
``lint``).  Pytest is never auto-enabled — running the test suite is a
big-blast-radius operation and the planner must ask for it.

Failure modes (``status="failed"``):

* ``PATH_OUTSIDE_WORKSPACE``    — path escapes ``workspace_root``.
* ``FILE_NOT_FOUND``            — target does not exist.
* ``NOT_A_FILE``                — target is a directory or special file.
* ``NO_RECEIPT_FOR_PATH``       — model has not read or patched this file
                                   in the current task.
* ``READ_FAILED``               — could not read the file content.
* ``UNKNOWN_CHECK``             — caller asked for a check we do not know.
* ``PYTEST_PATH_OUTSIDE_WORKSPACE`` — ``test_path`` escapes workspace.
* ``PYTEST_PATH_NOT_FOUND``     — ``test_path`` does not exist.

The tool itself never returns ``status="failed"`` for a *check* failure —
a check that runs and reports problems is a successful invocation that
returned ``overall_passed=false``.  Only invocation-level errors (above)
flip ``status``.
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ...kernel.receipt_manager import ReceiptManager
from ...protocol.invocation import ToolInvocation
from ...protocol.manifest import (
    PolicyRequirements,
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    VerifierSpec,
)
from ...protocol.result import ToolError, ToolResult
from ..base import Tool

_KNOWN_CHECKS = ("syntax", "lint", "pytest")
_OUTPUT_TRUNCATE = 4000

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Workspace-relative or absolute path to verify. The model must "
                "have read this file (read_file) or just patched it (patch_file) "
                "in the current task — otherwise the call is rejected with "
                "NO_RECEIPT_FOR_PATH."
            ),
        },
        "checks": {
            "type": "array",
            "items": {"type": "string", "enum": list(_KNOWN_CHECKS)},
            "description": (
                "Which checks to run. If omitted, the verifier auto-detects "
                "based on the file extension: .py → [syntax, lint], .json → "
                "[syntax], .yaml/.yml → [syntax]. pytest is never auto-enabled."
            ),
        },
        "test_path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Workspace-relative or absolute path to a test file or directory "
                "for the pytest check. Required when checks contains 'pytest'. "
                "The path must live inside the workspace."
            ),
        },
        "reason": {
            "type": "string",
            "description": "Free-form rationale for the audit trail.",
        },
    },
}

_RESULT_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["check", "passed", "skipped"],
    "properties": {
        "check": {"type": "string"},
        "passed": {"type": "boolean"},
        "skipped": {
            "type": "boolean",
            "description": (
                "True if the check did not apply (e.g. lint on a non-Python file). "
                "Skipped checks count as passed=true."
            ),
        },
        "reason": {
            "type": "string",
            "description": "Why the check was skipped (only when skipped=true).",
        },
        "error": {
            "type": "string",
            "description": "Short error message when passed=false.",
        },
        "output": {
            "type": "string",
            "description": (
                "Tool output excerpt (stdout/stderr) truncated to ~4 KB. "
                "Used by the model to decide on the next action."
            ),
        },
        "exit_code": {
            "type": "integer",
            "description": "Subprocess exit code for lint/pytest checks.",
        },
        "test_path": {
            "type": "string",
            "description": "For pytest: resolved test target.",
        },
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "path",
        "file_type",
        "checks_run",
        "overall_passed",
        "results",
    ],
    "properties": {
        "path": {"type": "string"},
        "file_type": {
            "type": "string",
            "enum": ["python", "json", "yaml", "toml", "markdown", "text", "unknown"],
        },
        "checks_run": {
            "type": "array",
            "items": {"type": "string"},
        },
        "overall_passed": {
            "type": "boolean",
            "description": (
                "True iff every non-skipped check passed. Skipped checks are "
                "treated as passed."
            ),
        },
        "results": {
            "type": "array",
            "items": _RESULT_ITEM_SCHEMA,
        },
    },
}


class VerifyFileTool(Tool):
    """Receipt-gated, workspace-bounded verifier.

    The tool is read-only with respect to the workspace, but it does run
    out-of-process subprocesses (``ruff``, ``pytest``).  Both subprocesses
    are constrained to the workspace via ``cwd=workspace_root``; their
    timeouts are bounded; their output is truncated.
    """

    def __init__(
        self,
        *,
        workspace_root: str,
        receipts: ReceiptManager,
    ) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.verify_file",
                name="verify_file",
                namespace="core.fs",
                version="1.0.0",
                description=(
                    "Verify a workspace file (syntax, lint, optional pytest). "
                    "Receipt-gated: only files the model has read or patched in "
                    "the current task can be verified. Never triggers rollback; "
                    "the planner reads the structured result and decides."
                ),
                summary="Run syntax/lint/pytest checks on a file the model has worked on.",
                capabilities=["fs.read", "process.spawn", "core.verify_file"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=180, max_file_size_mb=10),
                sandbox=SandboxSpec(profile="read_only_file_access"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._workspace = Path(workspace_root).resolve()
        self._receipts = receipts

    # ------------------------------------------------------------------ #

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        inp = invocation.input
        raw_path = str(inp["path"])
        explicit_checks: list[str] | None = (
            list(inp["checks"]) if "checks" in inp and inp["checks"] is not None else None
        )
        raw_test_path = inp.get("test_path")

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

        # ---- Receipt gate ---------------------------------------------------
        receipt = self._receipts.get_file_read(invocation.task_id, str(target))
        if receipt is None:
            return _failure(
                invocation,
                code="NO_RECEIPT_FOR_PATH",
                message=(
                    f"no read/patch receipt for '{raw_path}' in this task. "
                    f"Call read_file (or patch_file) on this path first; the verifier "
                    f"refuses to validate files the model has not worked on."
                ),
            )

        file_type = _detect_type(target)

        # ---- Determine which checks to run --------------------------------
        if explicit_checks is None:
            checks_to_run = _default_checks_for(file_type)
        else:
            for c in explicit_checks:
                if c not in _KNOWN_CHECKS:
                    return _failure(
                        invocation,
                        code="UNKNOWN_CHECK",
                        message=(
                            f"unknown check '{c}'. Allowed: {', '.join(_KNOWN_CHECKS)}."
                        ),
                    )
            checks_to_run = list(dict.fromkeys(explicit_checks))  # de-dup, stable

        # ---- Pytest path validation (if requested) ------------------------
        resolved_test_path: Path | None = None
        if "pytest" in checks_to_run:
            if raw_test_path is None:
                return _failure(
                    invocation,
                    code="PYTEST_TEST_PATH_REQUIRED",
                    message=(
                        "pytest check requires test_path (workspace path to a test file "
                        "or directory)."
                    ),
                )
            resolved_test_path = self._resolve(str(raw_test_path))
            if resolved_test_path is None:
                return _failure(
                    invocation,
                    code="PYTEST_PATH_OUTSIDE_WORKSPACE",
                    message=f"test_path escapes workspace: {raw_test_path}",
                )
            if not resolved_test_path.exists():
                return _failure(
                    invocation,
                    code="PYTEST_PATH_NOT_FOUND",
                    message=f"test_path not found: {raw_test_path}",
                )

        # ---- Read content (for syntax checks) -----------------------------
        try:
            content_bytes = target.read_bytes()
        except OSError as exc:
            return _failure(
                invocation,
                code="READ_FAILED",
                message=f"failed to read {raw_path}: {exc}",
            )
        try:
            content_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content_text = content_bytes.decode("utf-8", errors="replace")

        # ---- Run each requested check -------------------------------------
        results: list[dict[str, Any]] = []
        for check in checks_to_run:
            if check == "syntax":
                results.append(_run_syntax_check(file_type, content_text))
            elif check == "lint":
                results.append(self._run_lint_check(file_type, target))
            elif check == "pytest":
                assert resolved_test_path is not None  # validated above
                results.append(self._run_pytest_check(resolved_test_path))

        overall_passed = all(r["passed"] for r in results)

        output: dict[str, Any] = {
            "path": str(target),
            "file_type": file_type,
            "checks_run": list(checks_to_run),
            "overall_passed": overall_passed,
            "results": results,
        }

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

    # ---- Checks --------------------------------------------------------- #

    def _run_lint_check(self, file_type: str, target: Path) -> dict[str, Any]:
        if file_type != "python":
            return {
                "check": "lint",
                "passed": True,
                "skipped": True,
                "reason": f"lint applies only to python files (got {file_type})",
            }
        try:
            proc = subprocess.run(
                ["ruff", "check", str(target), "--output-format=concise"],
                capture_output=True,
                text=True,
                cwd=str(self._workspace),
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            return {
                "check": "lint",
                "passed": False,
                "skipped": False,
                "error": "ruff not found in PATH",
                "output": "ruff is required for the lint check; install it (pip install ruff).",
            }
        except subprocess.TimeoutExpired:
            return {
                "check": "lint",
                "passed": False,
                "skipped": False,
                "error": "ruff timed out",
                "output": "ruff exceeded the 30s budget for this file.",
            }

        combined = _join_streams(proc.stdout, proc.stderr)
        passed = proc.returncode == 0
        result: dict[str, Any] = {
            "check": "lint",
            "passed": passed,
            "skipped": False,
            "exit_code": int(proc.returncode),
            "output": combined,
        }
        if not passed:
            result["error"] = _first_line(combined) or f"ruff exit={proc.returncode}"
        return result

    def _run_pytest_check(self, test_path: Path) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                [
                    "python",
                    "-m",
                    "pytest",
                    str(test_path),
                    "--tb=short",
                    "-q",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                ],
                capture_output=True,
                text=True,
                cwd=str(self._workspace),
                timeout=120,
                check=False,
            )
        except FileNotFoundError:
            return {
                "check": "pytest",
                "passed": False,
                "skipped": False,
                "error": "python interpreter not found in PATH",
                "output": "",
                "test_path": str(test_path),
            }
        except subprocess.TimeoutExpired:
            return {
                "check": "pytest",
                "passed": False,
                "skipped": False,
                "error": "pytest timed out after 120s",
                "output": "Tests exceeded the 120s budget. Narrow test_path.",
                "test_path": str(test_path),
            }

        combined = _join_streams(proc.stdout, proc.stderr)
        passed = proc.returncode == 0
        result: dict[str, Any] = {
            "check": "pytest",
            "passed": passed,
            "skipped": False,
            "exit_code": int(proc.returncode),
            "output": combined,
            "test_path": str(test_path),
        }
        if not passed:
            result["error"] = _last_line(combined) or f"pytest exit={proc.returncode}"
        return result


# ---------------------------------------------------------------------------#
# Internal helpers                                                          #
# ---------------------------------------------------------------------------#


def _detect_type(target: Path) -> str:
    suffix = target.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix == ".json":
        return "json"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".toml":
        return "toml"
    if suffix in (".md", ".markdown"):
        return "markdown"
    if suffix in (".txt", ".rst", ".cfg", ".ini"):
        return "text"
    return "unknown"


def _default_checks_for(file_type: str) -> list[str]:
    """Auto-detection: only cheap file-local checks. Never pytest."""
    if file_type == "python":
        return ["syntax", "lint"]
    if file_type in ("json", "yaml"):
        return ["syntax"]
    return []


def _run_syntax_check(file_type: str, content: str) -> dict[str, Any]:
    if file_type == "python":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return {
                "check": "syntax",
                "passed": False,
                "skipped": False,
                "error": f"SyntaxError: {exc.msg} (line {exc.lineno}, col {exc.offset})",
                "output": _format_syntax_error(content, exc),
            }
        return {"check": "syntax", "passed": True, "skipped": False}

    if file_type == "json":
        try:
            json.loads(content) if content.strip() else None
        except json.JSONDecodeError as exc:
            return {
                "check": "syntax",
                "passed": False,
                "skipped": False,
                "error": f"JSONDecodeError: {exc.msg} (line {exc.lineno}, col {exc.colno})",
            }
        return {"check": "syntax", "passed": True, "skipped": False}

    if file_type == "yaml":
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            return {
                "check": "syntax",
                "passed": False,
                "skipped": False,
                "error": f"YAMLError: {str(exc)[:300]}",
            }
        return {"check": "syntax", "passed": True, "skipped": False}

    return {
        "check": "syntax",
        "passed": True,
        "skipped": True,
        "reason": f"no syntax check defined for file_type={file_type}",
    }


def _format_syntax_error(content: str, exc: SyntaxError) -> str:
    lineno = exc.lineno or 0
    if lineno <= 0:
        return exc.msg or ""
    lines = content.splitlines()
    start = max(0, lineno - 3)
    end = min(len(lines), lineno + 2)
    out: list[str] = []
    for idx in range(start, end):
        marker = ">>" if idx == lineno - 1 else "  "
        out.append(f"{marker} {idx + 1:4d}: {lines[idx]}")
    return "\n".join(out)


def _join_streams(stdout: str, stderr: str) -> str:
    parts: list[str] = []
    if stdout:
        parts.append(stdout.rstrip())
    if stderr:
        parts.append(stderr.rstrip())
    combined = "\n".join(parts).rstrip()
    if len(combined) > _OUTPUT_TRUNCATE:
        head = combined[: _OUTPUT_TRUNCATE]
        return f"{head}\n... [truncated {len(combined) - _OUTPUT_TRUNCATE} chars]"
    return combined


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:300]
    return ""


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line:
            return line[:300]
    return ""


def _failure(
    invocation: ToolInvocation,
    *,
    code: str,
    message: str,
) -> ToolResult:
    non_retryable = {
        "PATH_OUTSIDE_WORKSPACE",
        "NOT_A_FILE",
        "UNKNOWN_CHECK",
        "PYTEST_PATH_OUTSIDE_WORKSPACE",
        "PYTEST_TEST_PATH_REQUIRED",
    }
    return ToolResult(
        invocation_id=invocation.invocation_id,
        task_id=invocation.task_id,
        step_id=invocation.step_id,
        status="failed",
        error=ToolError(
            kind="VerificationError",
            code=code,
            message=message,
            retryable=code not in non_retryable,
            requires_diagnosis=code in {
                "FILE_NOT_FOUND",
                "READ_FAILED",
                "NO_RECEIPT_FOR_PATH",
                "PYTEST_PATH_NOT_FOUND",
            },
        ),
    )
