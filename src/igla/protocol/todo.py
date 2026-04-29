"""TODO tree — branching task decomposition.

A TodoNode is a node in a DAG (typically a tree) that represents one piece
of intent. Nodes can branch on clarification: a node in status
``CLARIFYING`` accumulates child nodes that materialise the answer to the
clarification question and inherit the parent's goal context.

Hard rules (enforced by ``todo.tree``):

* A leaf node may have zero children.
* A non-leaf node's status is derived from its children: it is ``DONE`` iff
  all children are ``DONE`` or ``ABANDONED`` (with at least one ``DONE``); it
  is ``BLOCKED`` if any child is ``BLOCKED``; otherwise propagates the most
  active child's status.
* A node in ``CLARIFYING`` cannot be transitioned to ``DONE`` directly.
* A node moves to ``ABANDONED`` only with an explicit reason.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TodoStatus(str, Enum):
    PROPOSED = "proposed"  # just created by planner, not yet started
    CLARIFYING = "clarifying"  # awaiting user clarification
    READY = "ready"  # ready to spawn steps
    IN_PROGRESS = "in_progress"  # at least one step is RUNNING
    BLOCKED = "blocked"
    DONE = "done"
    ABANDONED = "abandoned"


class TodoNodeKind(str, Enum):
    GOAL = "goal"  # root: the original user intent
    SUBGOAL = "subgoal"  # nested sub-task created by the planner
    CLARIFICATION = "clarification"  # ask-the-user node (one question per node)
    ACTION = "action"  # leaf bound to one PlanStep


class TodoNode(BaseModel):
    """One immutable snapshot of a TODO node.

    Mutations always go through ``TodoTree`` and are persisted as a new
    snapshot; the live tree holds the latest version per ``node_id``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    task_id: str
    parent_id: str | None = None

    kind: TodoNodeKind
    title: str
    description: str | None = None

    status: TodoStatus = TodoStatus.PROPOSED

    # For clarification nodes
    clarification_question: str | None = None
    clarification_answer: str | None = None

    # For action nodes
    bound_step_id: str | None = None

    # Linked artifacts/evidence (refs only)
    evidence_refs: list[str] = Field(default_factory=list)

    children_ids: list[str] = Field(default_factory=list)
    depth: int = 0
    sequence: int = 0  # ordering hint among siblings

    # Authoring & timing
    created_by: str = "planner"  # planner | user | runtime
    created_at: datetime
    updated_at: datetime
    abandon_reason: str | None = None


class TodoTreeSnapshot(BaseModel):
    """A full snapshot of all TODO nodes for a task — useful for prompts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    root_id: str
    nodes: list[TodoNode] = Field(default_factory=list)
    captured_at: datetime
