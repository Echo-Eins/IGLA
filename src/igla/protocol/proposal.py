"""Planner proposal envelope.

The LM Studio model returns one of these via structured output. The kernel
never trusts the model's free-form text; it parses one of these typed
proposals or rejects the response as malformed.

The discriminator is the ``action`` field. Adding a new proposal kind:

1. Add a new ``...Proposal`` model below.
2. Add it to ``ProposalKind`` and to the ``Annotated[Union, Discriminator]``.
3. Add a corresponding ``ActionRequest.kind`` literal in ``policy.py``.
4. Register a handler in the planner loop.

The string fields below carry deliberately tight ``pattern`` constraints.
LM Studio compiles the JSON Schema into a logits-processor / grammar so the
model literally cannot emit characters outside the pattern. This blocks the
common "small-model fallback to placeholder strings" failure where a tired
model emits ``tool_name="... ... ..."`` or ``tool_version="v1.??? ?"`` and
burns iterations until MAX_CONSECUTIVE_REJECTIONS. The same constraints are
re-checked by Pydantic on the way in, so even a server that ignores the
schema cannot smuggle garbage past.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel

# Identifier-like name: ASCII letter/underscore + word/dash, up to 64 chars.
# Matches every built-in tool name (find_files, search_text, read_file,
# ask_user, noop_observe) and any reasonable future tool naming convention.
_TOOL_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$"

# Permissive semver-like version: digits, dots, hyphens, plus signs, and an
# optional leading ``v``. Matches ``1.0.0``, ``1.2.3-rc.1``, ``v1.0.0``, etc.
# Hyphen is placed first in the character class to avoid GBNF \- escape issue.
_TOOL_VERSION_PATTERN = r"^v?[0-9][-0-9A-Za-z._+]{0,31}$"

# TODO node ids are produced by ``ids.prefixed_id`` (e.g. ``8JGKWK`` /
# ``node_01KQF...``). Allow ASCII alnum + underscore + dash, no spaces.
_NODE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ProposalKind:
    TOOL_INVOCATION = "tool_invocation"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    TODO_BRANCH = "todo_branch"
    TODO_COMPLETE = "todo_complete"
    DECLARE_TASK_DONE = "declare_task_done"


class _BaseProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, description="Why this action is being proposed.")
    target_todo_node_id: str | None = Field(default=None, pattern=_NODE_ID_PATTERN)


class ToolInvocationProposal(_BaseProposal):
    action: Literal["tool_invocation"] = "tool_invocation"
    tool_name: str = Field(..., pattern=_TOOL_NAME_PATTERN)
    tool_version: str = Field(..., pattern=_TOOL_VERSION_PATTERN)
    input: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: list[dict[str, Any]] = Field(default_factory=list)


class AskUserClarificationProposal(_BaseProposal):
    action: Literal["ask_user_clarification"] = "ask_user_clarification"
    question: str = Field(..., min_length=1)
    creates_clarification_node: bool = True


class TodoBranchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1)
    description: str | None = None
    kind: Literal["subgoal", "clarification", "action"] = "subgoal"
    clarification_question: str | None = None


class TodoBranchProposal(_BaseProposal):
    action: Literal["todo_branch"] = "todo_branch"
    parent_node_id: str = Field(..., pattern=_NODE_ID_PATTERN)
    children: list[TodoBranchSpec] = Field(default_factory=list, min_length=1)


class TodoCompleteProposal(_BaseProposal):
    action: Literal["todo_complete"] = "todo_complete"
    node_id: str = Field(..., pattern=_NODE_ID_PATTERN)
    summary: str | None = None


class DeclareTaskDoneProposal(_BaseProposal):
    action: Literal["declare_task_done"] = "declare_task_done"
    summary: str = Field(..., min_length=1)


PlannerProposalUnion = Annotated[
    Union[
        ToolInvocationProposal,
        AskUserClarificationProposal,
        TodoBranchProposal,
        TodoCompleteProposal,
        DeclareTaskDoneProposal,
    ],
    Field(discriminator="action"),
]


class PlannerProposal(RootModel[PlannerProposalUnion]):
    """Discriminated wrapper. Use ``.root`` to access the concrete model."""

    @property
    def kind(self) -> str:
        return self.root.action

    @property
    def reason(self) -> str:
        return self.root.reason

    @property
    def target_todo_node_id(self) -> str | None:
        return self.root.target_todo_node_id
