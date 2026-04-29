"""``noop_observe`` — record a structured observation without doing anything.

Useful when the planner wants to log a thought-step that is **part of the
plan** (and therefore must consume an iteration) but does not need any
real side effect. Records its ``note`` payload as the tool's output.
"""
from __future__ import annotations

from ...protocol.invocation import ToolInvocation
from ...protocol.manifest import (
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    VerifierSpec,
)
from ...protocol.result import ToolResult
from ..base import Tool

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["note"],
    "properties": {
        "note": {"type": "string", "minLength": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["note"],
    "properties": {
        "note": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


class NoopObserveTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.noop_observe",
                name="noop_observe",
                namespace="core.observe",
                version="1.0.0",
                description=(
                    "Record a structured observation/note. Has no side effects; "
                    "useful for logging planner reasoning as a kernel-visible event."
                ),
                capabilities=["observe.note"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                resources=ResourceLimits(timeout_seconds=5),
                sandbox=SandboxSpec(profile="in_process"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={
                "note": invocation.input["note"],
                "tags": list(invocation.input.get("tags", [])),
            },
        )
