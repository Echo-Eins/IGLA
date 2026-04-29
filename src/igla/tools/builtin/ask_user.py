"""``ask_user`` tool — the only sanctioned channel for tool-driven user I/O.

The chat REPL injects a concrete ``AskUserChannel`` implementation. Tests
inject a scripted channel to drive the planner without a TTY.
"""
from __future__ import annotations

from typing import Protocol

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


class AskUserChannel(Protocol):
    """Interface the tool uses to communicate with the user."""

    def ask(self, *, question: str, prompt_label: str | None = None) -> str: ...


class ConsoleAskUserChannel:
    """Simple ``input()``-based channel for the CLI."""

    def ask(self, *, question: str, prompt_label: str | None = None) -> str:
        label = prompt_label or "you"
        # The chat REPL renders this question more nicely; this fallback is
        # used by ad-hoc CLI/test contexts.
        print(f"\nИГЛА → {question}")
        try:
            return input(f"{label}> ")
        except EOFError:
            return ""


_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question"],
    "properties": {
        "question": {"type": "string", "minLength": 1},
        "prompt_label": {"type": "string"},
    },
}


_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {
        "answer": {"type": "string"},
        "trimmed": {"type": "boolean"},
    },
}


class AskUserTool(Tool):
    def __init__(self, channel: AskUserChannel) -> None:
        super().__init__(
            ToolManifest(
                id="tool.core.ask_user",
                name="ask_user",
                namespace="core.user_io",
                version="1.0.0",
                description=(
                    "Ask the user a single, focused question through the chat channel. "
                    "Returns the raw answer. Cannot be used to ask multiple questions "
                    "in one call; use multiple invocations or a clarification subtree."
                ),
                summary="Ask the user a question.",
                capabilities=["user.ask"],
                risk_level="read_only",
                side_effects=False,
                input_schema=_INPUT_SCHEMA,
                output_schema=_OUTPUT_SCHEMA,
                policies=PolicyRequirements(),
                resources=ResourceLimits(timeout_seconds=600),
                sandbox=SandboxSpec(profile="in_process"),
                verifier=VerifierSpec(type="schema_validation"),
            )
        )
        self._channel = channel

    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        question = invocation.input.get("question", "").strip()
        prompt_label = invocation.input.get("prompt_label")
        if not question:
            return ToolResult(
                invocation_id=invocation.invocation_id,
                task_id=invocation.task_id,
                step_id=invocation.step_id,
                status="failed",
                error=ToolError(
                    kind="ValidationError",
                    code="EMPTY_QUESTION",
                    message="ask_user requires a non-empty question",
                ),
            )
        answer = self._channel.ask(question=question, prompt_label=prompt_label)
        trimmed = answer.strip() != answer
        return ToolResult(
            invocation_id=invocation.invocation_id,
            task_id=invocation.task_id,
            step_id=invocation.step_id,
            status="success",
            output={"answer": answer.strip(), "trimmed": trimmed},
        )
