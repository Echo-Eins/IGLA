"""TodoTree behaviour."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from igla.kernel.clock import StepClock
from igla.protocol.todo import TodoNodeKind, TodoStatus
from igla.todo.tree import TodoTree


def make_tree() -> TodoTree:
    clock = StepClock(start=datetime(2026, 4, 29, tzinfo=timezone.utc), step_seconds=0.1)
    return TodoTree("task_test", clock)


def test_create_root() -> None:
    tree = make_tree()
    root = tree.create_root(title="goal")
    assert tree.root_id == root.node_id
    assert tree.has(root.node_id)


def test_clarification_branch_and_answer() -> None:
    tree = make_tree()
    root = tree.create_root(title="g")
    clar = tree.add_child(
        root.node_id,
        kind=TodoNodeKind.CLARIFICATION,
        title="ask",
        clarification_question="where?",
    )
    assert clar.status is TodoStatus.CLARIFYING
    answered = tree.record_clarification_answer(clar.node_id, "/tmp")
    assert answered.status is TodoStatus.DONE
    assert answered.clarification_answer == "/tmp"


def test_action_node_cannot_have_children() -> None:
    tree = make_tree()
    root = tree.create_root(title="g")
    action = tree.add_child(
        root.node_id, kind=TodoNodeKind.ACTION, title="do thing"
    )
    with pytest.raises(ValueError):
        tree.add_child(action.node_id, kind=TodoNodeKind.SUBGOAL, title="x")


def test_status_propagates_to_parent_when_all_children_done() -> None:
    tree = make_tree()
    root = tree.create_root(title="g")
    a = tree.add_child(root.node_id, kind=TodoNodeKind.ACTION, title="A")
    b = tree.add_child(root.node_id, kind=TodoNodeKind.ACTION, title="B")
    tree.set_status(a.node_id, TodoStatus.DONE)
    parent = tree.get(root.node_id)
    assert parent.status is TodoStatus.IN_PROGRESS  # because B not yet
    tree.set_status(b.node_id, TodoStatus.DONE)
    parent = tree.get(root.node_id)
    assert parent.status is TodoStatus.DONE


def test_blocked_child_blocks_parent() -> None:
    tree = make_tree()
    root = tree.create_root(title="g")
    child = tree.add_child(root.node_id, kind=TodoNodeKind.ACTION, title="A")
    tree.set_status(child.node_id, TodoStatus.BLOCKED)
    assert tree.get(root.node_id).status is TodoStatus.BLOCKED


def test_max_depth_tracks_branches() -> None:
    tree = make_tree()
    root = tree.create_root(title="g")
    sub = tree.add_child(root.node_id, kind=TodoNodeKind.SUBGOAL, title="s")
    tree.add_child(sub.node_id, kind=TodoNodeKind.ACTION, title="a")
    assert tree.max_depth() == 2


def test_abandoned_requires_reason() -> None:
    tree = make_tree()
    root = tree.create_root(title="g")
    a = tree.add_child(root.node_id, kind=TodoNodeKind.ACTION, title="A")
    with pytest.raises(ValueError):
        tree.set_status(a.node_id, TodoStatus.ABANDONED)
    tree.set_status(a.node_id, TodoStatus.ABANDONED, abandon_reason="user changed mind")
    assert tree.get(a.node_id).status is TodoStatus.ABANDONED


def test_snapshot_round_trip(tmp_path) -> None:
    from igla.todo.store import TodoStore

    tree = make_tree()
    tree.create_root(title="g")
    store = TodoStore(tmp_path)
    path = store.save(tree)
    assert path.exists()
    snapshot = store.load_snapshot(tree.task_id)
    assert snapshot is not None
    assert snapshot.task_id == tree.task_id
    assert len(snapshot.nodes) == 1
