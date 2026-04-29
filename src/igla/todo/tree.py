"""Live mutable TODO tree for one task.

The tree is a thin in-memory structure plus a list of immutable snapshots
of every change. The kernel reads the live tree and snapshots are persisted
by ``TodoStore`` (``store.py``).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..ids import prefixed_id
from ..kernel.clock import Clock
from ..protocol.todo import (
    TodoNode,
    TodoNodeKind,
    TodoStatus,
    TodoTreeSnapshot,
)


@dataclass
class _Mutable:
    """Mutable shadow of a TodoNode used internally to avoid Pydantic
    re-construction for every status update."""

    node: TodoNode

    def to_snapshot(self) -> TodoNode:
        return self.node


class TodoTree:
    def __init__(self, task_id: str, clock: Clock) -> None:
        self._task_id = task_id
        self._clock = clock
        self._nodes: dict[str, _Mutable] = {}
        self._root_id: str | None = None

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def root_id(self) -> str:
        if self._root_id is None:
            raise RuntimeError("todo tree has no root yet")
        return self._root_id

    def __len__(self) -> int:
        return len(self._nodes)

    def has(self, node_id: str) -> bool:
        return node_id in self._nodes

    # --- creation -------------------------------------------------------

    def create_root(self, *, title: str, description: str | None = None) -> TodoNode:
        if self._root_id is not None:
            raise RuntimeError("root node already exists")
        node = self._make_node(
            parent_id=None,
            kind=TodoNodeKind.GOAL,
            title=title,
            description=description,
            depth=0,
            sequence=0,
            created_by="user",
        )
        self._nodes[node.node_id] = _Mutable(node)
        self._root_id = node.node_id
        return node

    def add_child(
        self,
        parent_id: str,
        *,
        kind: TodoNodeKind,
        title: str,
        description: str | None = None,
        clarification_question: str | None = None,
        bound_step_id: str | None = None,
        created_by: str = "planner",
    ) -> TodoNode:
        parent = self._require(parent_id)
        if parent.node.kind == TodoNodeKind.ACTION:
            raise ValueError("ACTION nodes cannot have children")
        sequence = len(parent.node.children_ids)
        depth = parent.node.depth + 1
        node = self._make_node(
            parent_id=parent_id,
            kind=kind,
            title=title,
            description=description,
            clarification_question=clarification_question,
            bound_step_id=bound_step_id,
            depth=depth,
            sequence=sequence,
            created_by=created_by,
        )
        self._nodes[node.node_id] = _Mutable(node)
        # Update parent's children
        new_parent = parent.node.model_copy(
            update={
                "children_ids": [*parent.node.children_ids, node.node_id],
                "updated_at": self._clock.now(),
                "status": TodoStatus.IN_PROGRESS
                if parent.node.status == TodoStatus.PROPOSED
                else parent.node.status,
            }
        )
        parent.node = new_parent
        return node

    # --- mutation -------------------------------------------------------

    def set_status(
        self,
        node_id: str,
        status: TodoStatus,
        *,
        abandon_reason: str | None = None,
    ) -> TodoNode:
        slot = self._require(node_id)
        update: dict[str, object] = {"status": status, "updated_at": self._clock.now()}
        if status == TodoStatus.ABANDONED:
            if not abandon_reason:
                raise ValueError("ABANDONED requires abandon_reason")
            update["abandon_reason"] = abandon_reason
        slot.node = slot.node.model_copy(update=update)
        # Propagate status upwards.
        if slot.node.parent_id is not None:
            self._recompute_parent(slot.node.parent_id)
        return slot.node

    def record_clarification_answer(self, node_id: str, answer: str) -> TodoNode:
        slot = self._require(node_id)
        if slot.node.kind != TodoNodeKind.CLARIFICATION:
            raise ValueError("only CLARIFICATION nodes accept answers")
        slot.node = slot.node.model_copy(
            update={
                "clarification_answer": answer,
                "status": TodoStatus.DONE,
                "updated_at": self._clock.now(),
            }
        )
        if slot.node.parent_id is not None:
            self._recompute_parent(slot.node.parent_id)
        return slot.node

    def bind_step(self, node_id: str, step_id: str) -> TodoNode:
        slot = self._require(node_id)
        if slot.node.kind != TodoNodeKind.ACTION:
            raise ValueError("only ACTION nodes can be bound to a step")
        slot.node = slot.node.model_copy(
            update={"bound_step_id": step_id, "updated_at": self._clock.now()}
        )
        return slot.node

    def attach_evidence(self, node_id: str, evidence_ids: Iterable[str]) -> TodoNode:
        slot = self._require(node_id)
        merged = list(dict.fromkeys([*slot.node.evidence_refs, *evidence_ids]))
        slot.node = slot.node.model_copy(
            update={"evidence_refs": merged, "updated_at": self._clock.now()}
        )
        return slot.node

    # --- queries --------------------------------------------------------

    def get(self, node_id: str) -> TodoNode:
        return self._require(node_id).node

    def all_nodes(self) -> list[TodoNode]:
        return [slot.node for slot in self._nodes.values()]

    def open_nodes(self) -> list[TodoNode]:
        active = {
            TodoStatus.PROPOSED,
            TodoStatus.READY,
            TodoStatus.IN_PROGRESS,
            TodoStatus.CLARIFYING,
            TodoStatus.BLOCKED,
        }
        return [slot.node for slot in self._nodes.values() if slot.node.status in active]

    def max_depth(self) -> int:
        if not self._nodes:
            return 0
        return max(slot.node.depth for slot in self._nodes.values())

    def snapshot(self) -> TodoTreeSnapshot:
        return TodoTreeSnapshot(
            task_id=self._task_id,
            root_id=self._root_id or "",
            nodes=[slot.node for slot in self._nodes.values()],
            captured_at=self._clock.now(),
        )

    # --- internals ------------------------------------------------------

    def _make_node(
        self,
        *,
        parent_id: str | None,
        kind: TodoNodeKind,
        title: str,
        description: str | None,
        depth: int,
        sequence: int,
        created_by: str,
        clarification_question: str | None = None,
        bound_step_id: str | None = None,
    ) -> TodoNode:
        now = self._clock.now()
        initial_status = (
            TodoStatus.CLARIFYING if kind == TodoNodeKind.CLARIFICATION else TodoStatus.PROPOSED
        )
        return TodoNode(
            node_id=prefixed_id("todo"),
            task_id=self._task_id,
            parent_id=parent_id,
            kind=kind,
            title=title,
            description=description,
            status=initial_status,
            clarification_question=clarification_question,
            bound_step_id=bound_step_id,
            depth=depth,
            sequence=sequence,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def _require(self, node_id: str) -> _Mutable:
        slot = self._nodes.get(node_id)
        if slot is None:
            raise KeyError(f"todo node not found: {node_id}")
        return slot

    def _recompute_parent(self, parent_id: str) -> None:
        slot = self._nodes.get(parent_id)
        if slot is None:
            return
        children = [self._nodes[c].node for c in slot.node.children_ids if c in self._nodes]
        new_status = _aggregate_status(slot.node.status, children)
        if new_status != slot.node.status:
            slot.node = slot.node.model_copy(
                update={"status": new_status, "updated_at": self._clock.now()}
            )
            if slot.node.parent_id is not None:
                self._recompute_parent(slot.node.parent_id)


def _aggregate_status(current: TodoStatus, children: list[TodoNode]) -> TodoStatus:
    """Derive a parent's status from its children.

    * Any BLOCKED child → BLOCKED.
    * All children DONE/ABANDONED with at least one DONE → DONE.
    * Any IN_PROGRESS / CLARIFYING child → IN_PROGRESS.
    * Otherwise keep current.
    """
    if not children:
        return current
    if any(c.status == TodoStatus.BLOCKED for c in children):
        return TodoStatus.BLOCKED
    if all(c.status in (TodoStatus.DONE, TodoStatus.ABANDONED) for c in children) and any(
        c.status == TodoStatus.DONE for c in children
    ):
        return TodoStatus.DONE
    if any(c.status in (TodoStatus.IN_PROGRESS, TodoStatus.CLARIFYING) for c in children):
        return TodoStatus.IN_PROGRESS
    return current
