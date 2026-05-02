"""Executor boundary behavior."""
from __future__ import annotations

from igla.kernel.executor import Executor, ExecutorContext
from igla.kernel.registry import ToolRegistry
from igla.kernel.schema_validator import SchemaValidator
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.protocol.manifest import ToolManifest
from igla.protocol.result import ToolError, ToolResult
from igla.tools.base import Tool

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["value"],
    "properties": {"value": {"type": "string"}},
}


class _FailingTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            ToolManifest(
                id="tool.test.failing",
                name="failing_tool",
                namespace="test",
                version="1.0.0",
                description="Returns a structured failure without success output.",
                output_schema=_OUTPUT_SCHEMA,
            )
        )

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="failed",
            error=ToolError(
                kind="FileSystemError",
                code="FILE_TOO_LARGE",
                message="read iteratively",
            ),
        )


class _BadSuccessOutputTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            ToolManifest(
                id="tool.test.bad_success",
                name="bad_success_tool",
                namespace="test",
                version="1.0.0",
                description="Returns invalid success output.",
                output_schema=_OUTPUT_SCHEMA,
            )
        )

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={},
        )


def _executor(tool: Tool) -> Executor:
    registry = ToolRegistry()
    registry.register(tool)
    return Executor(
        registry,
        SchemaValidator(),
        ExecutorContext(workspace_root="/workspace"),
    )


def test_failed_result_keeps_original_error_without_output_validation() -> None:
    invocation = ToolInvocation(
        invocation_id="inv_test",
        task_id="task_test",
        tool=ToolRef(name="failing_tool", version="1.0.0"),
    )

    result = _executor(_FailingTool()).invoke(invocation)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "FILE_TOO_LARGE"


def test_success_result_still_validates_output_schema() -> None:
    invocation = ToolInvocation(
        invocation_id="inv_test",
        task_id="task_test",
        tool=ToolRef(name="bad_success_tool", version="1.0.0"),
    )

    result = _executor(_BadSuccessOutputTool()).invoke(invocation)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "OUTPUT_SCHEMA_INVALID"
