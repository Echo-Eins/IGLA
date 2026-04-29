"""Planner proposal envelope.

The LM Studio model returns one of these via structured output. The kernel
never trusts the model's free-form text; it parses one of these typed
proposals or rejects the response as malformed.

The discriminator is the ``action`` field. Adding a new proposal kind:

1. Add a new ``...Proposal`` model below.
2. Add it to ``ProposalKind`` and to the ``Annotated[Union, Discriminator]``.
3. Add a corresponding ``ActionRequest.kind`` literal in ``policy.py``.
4. Register a handler in the planner loop.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel


class ProposalKind:
    TOOL_INVOCATION = "tool_invocation"
    ASK_USER_CLARIFICATION = "ask_user_clarification"
    TODO_BRANCH = "todo_branch"
    TODO_COMPLETE = "todo_complete"
    DECLARE_TASK_DONE = "declare_task_done"


class _BaseProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, description="Why this action is being proposed.")
    target_todo_node_id: str | None = None


class ToolInvocationProposal(_BaseProposal):
    action: Literal["tool_invocation"] = "tool_invocation"
    tool_name: str = Field(..., min_length=1)
    tool_version: str = Field(..., min_length=1)
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
    parent_node_id: str
    children: list[TodoBranchSpec] = Field(default_factory=list, min_length=1)


class TodoCompleteProposal(_BaseProposal):
    action: Literal["todo_complete"] = "todo_complete"
    node_id: str = Field(..., min_length=1)
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
