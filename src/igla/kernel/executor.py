"""Executor: runs ``Tool.invoke`` under kernel supervision.

In MVP-0 there is no sandbox, so tools execute in-process. Even so, the
executor enforces:

* manifest-declared input/output schema validation;
* a hard wall-clock timeout per invocation (signal-based on Linux);
* uniform conversion of Python exceptions into ``ToolResult.failed``.

Tools must NOT raise. If they do, we synthesise a failed ToolResult with
``error.kind="ToolException"`` so the rest of the kernel can keep going.
"""
from __future__ import annotations

import signal
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..protocol.invocation import ToolInvocation
from ..protocol.result import ToolError, ToolResult, ToolResultMetric
from .errors import ToolValidationError
from .schema_validator import SchemaValidator

if TYPE_CHECKING:  # pragma: no cover
    from .registry import ToolRegistry


@dataclass(frozen=True)
class ExecutorContext:
    """Lightweight context accessible to tools via ``Tool.context`` if they
    register a context-aware base. The MVP keeps this empty; the field is
    here so signatures don't break later."""

    workspace_root: str


class _Timeout(Exception):
    pass


@contextmanager
def _alarm_timeout(seconds: int) -> Iterator[None]:
    """Coarse SIGALRM-based timeout. Linux only.

    Tools running pure Python or subprocesses obey this on POSIX; tightly
    C-bound code may not. On platforms without SIGALRM (Windows) this is a
    no-op until the proper sandbox runner replaces it.
    """

    sigalarm = getattr(signal, "SIGALRM", None)
    alarm = getattr(signal, "alarm", None)
    if seconds <= 0 or sigalarm is None or not callable(alarm):
        yield
        return

    alarm_fn: Callable[[int], int] = alarm

    def _handler(signum: int, frame: object) -> None:
        del signum, frame
        raise _Timeout(f"tool timed out after {seconds}s")

    previous = signal.signal(sigalarm, _handler)
    alarm_fn(seconds)
    try:
        yield
    finally:
        alarm_fn(0)
        signal.signal(sigalarm, previous)


class Executor:
    def __init__(
        self,
        registry: "ToolRegistry",
        validator: SchemaValidator,
        context: ExecutorContext,
    ) -> None:
        self._registry = registry
        self._validator = validator
        self._context = context

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        manifest = self._registry.get(invocation.tool.name, invocation.tool.version)

        # Validate input
        try:
            self._validator.validate_input(manifest, invocation.input)
        except ToolValidationError as exc:
            return _result_failure(
                invocation,
                kind="ValidationError",
                code="INPUT_SCHEMA_INVALID",
                message=str(exc),
                details={"errors": exc.errors},
            )

        tool = self._registry.get_tool(manifest.name, manifest.version)
        timeout = max(
            1,
            int(invocation.policy.max_runtime_seconds or manifest.resources.timeout_seconds),
        )

        started = time.monotonic()
        try:
            with _alarm_timeout(timeout):
                result = tool.invoke(invocation)
        except _Timeout as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            return _result_failure(
                invocation,
                kind="Timeout",
                code="TOOL_TIMEOUT",
                message=str(exc),
                runtime_ms=elapsed,
            )
        except Exception as exc:  # noqa: BLE001 — boundary
            elapsed = int((time.monotonic() - started) * 1000)
            return _result_failure(
                invocation,
                kind="ToolException",
                code="UNEXPECTED_TOOL_EXCEPTION",
                message=str(exc) or exc.__class__.__name__,
                details={"traceback": traceback.format_exc()},
                runtime_ms=elapsed,
            )

        # Validate output
        try:
            self._validator.validate_output(manifest, result.output)
        except ToolValidationError as exc:
            return _result_failure(
                invocation,
                kind="ValidationError",
                code="OUTPUT_SCHEMA_INVALID",
                message=str(exc),
                details={"errors": exc.errors},
            )
        return result


def _result_failure(
    invocation: ToolInvocation,
    *,
    kind: str,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    runtime_ms: int | None = None,
) -> ToolResult:
    return ToolResult(
        invocation_id=invocation.invocation_id,
        task_id=invocation.task_id,
        step_id=invocation.step_id,
        status="failed",
        error=ToolError(
            kind=kind,
            code=code,
            message=message,
            retryable=False,
            requires_diagnosis=True,
            details=details or {},
        ),
        metrics=ToolResultMetric(runtime_ms=runtime_ms),
    )
