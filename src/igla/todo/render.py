"""Text/Markdown rendering of a TODO tree.

Used both for chat output and for the planner prompt.

The formatting rules are deliberate and stable so that the LLM can pattern-
match against them:

* Each line begins with status glyph + node kind shortcode + node_id (last 6
  chars) + indented title.
* Sub-trees are indented with two spaces per depth level.
* CLARIFICATION nodes show their question (and answer when present).
"""
from __future__ import annotations

from collections.abc import Iterable

from ..protocol.todo import TodoNode, TodoNodeKind, TodoStatus, TodoTreeSnapshot

_STATUS_GLYPH: dict[TodoStatus, str] = {
    TodoStatus.PROPOSED: "·",
    TodoStatus.READY: "○",
    TodoStatus.IN_PROGRESS: "►",
    TodoStatus.CLARIFYING: "?",
    TodoStatus.BLOCKED: "✕",
    TodoStatus.DONE: "✓",
    TodoStatus.ABANDONED: "—",
}

_KIND_TAG: dict[TodoNodeKind, str] = {
    TodoNodeKind.GOAL: "GOAL",
    TodoNodeKind.SUBGOAL: "SUB ",
    TodoNodeKind.CLARIFICATION: "ASK ",
    TodoNodeKind.ACTION: "ACT ",
}


def render_text(snapshot: TodoTreeSnapshot, *, with_ids: bool = True) -> str:
    if not snapshot.nodes:
        return "(empty TODO tree)"
    by_id = {n.node_id: n for n in snapshot.nodes}
    children: dict[str, list[str]] = {nid: [] for nid in by_id}
    for node in snapshot.nodes:
        if node.parent_id is not None and node.parent_id in children:
            children[node.parent_id].append(node.node_id)
    for nid in children:
        children[nid].sort(key=lambda c: by_id[c].sequence)

    lines: list[str] = []
    if snapshot.root_id and snapshot.root_id in by_id:
        _render_subtree(snapshot.root_id, by_id, children, lines, with_ids=with_ids)
    else:  # orphan defensively
        for node in snapshot.nodes:
            if node.parent_id is None:
                _render_subtree(node.node_id, by_id, children, lines, with_ids=with_ids)
    return "\n".join(lines)


def render_open_summary(snapshot: TodoTreeSnapshot, limit: int = 12) -> str:
    """Compact one-line summaries of open (not done/abandoned) nodes."""
    open_nodes = [
        n
        for n in snapshot.nodes
        if n.status
        in {
            TodoStatus.PROPOSED,
            TodoStatus.READY,
            TodoStatus.IN_PROGRESS,
            TodoStatus.CLARIFYING,
            TodoStatus.BLOCKED,
        }
    ]
    open_nodes.sort(key=lambda n: (n.depth, n.sequence))
    out: list[str] = []
    for node in open_nodes[:limit]:
        glyph = _STATUS_GLYPH.get(node.status, "?")
        tag = _KIND_TAG.get(node.kind, "????")
        out.append(f"{glyph} {tag} {node.node_id[-6:]} {node.title}")
    if len(open_nodes) > limit:
        out.append(f"… and {len(open_nodes) - limit} more")
    return "\n".join(out) or "(no open nodes)"


def _render_subtree(
    node_id: str,
    by_id: dict[str, TodoNode],
    children: dict[str, list[str]],
    out: list[str],
    *,
    with_ids: bool,
) -> None:
    node = by_id[node_id]
    indent = "  " * node.depth
    glyph = _STATUS_GLYPH.get(node.status, "?")
    tag = _KIND_TAG.get(node.kind, "????")
    suffix = f"  [{node.node_id[-6:]}]" if with_ids else ""
    line = f"{indent}{glyph} {tag} {node.title}{suffix}"
    out.append(line)
    if node.kind == TodoNodeKind.CLARIFICATION:
        if node.clarification_question:
            out.append(f"{indent}    Q: {node.clarification_question}")
        if node.clarification_answer:
            out.append(f"{indent}    A: {node.clarification_answer}")
    if node.abandon_reason:
        out.append(f"{indent}    abandoned: {node.abandon_reason}")
    for child_id in children.get(node_id, []):
        _render_subtree(child_id, by_id, children, out, with_ids=with_ids)


def render_dict_for_prompt(snapshot: TodoTreeSnapshot) -> dict[str, object]:
    """Compact, prompt-friendly dict version. Avoids large fields and timestamps."""
    return {
        "task_id": snapshot.task_id,
        "root_id": snapshot.root_id,
        "nodes": [
            {
                "id": n.node_id,
                "parent": n.parent_id,
                "kind": n.kind.value,
                "title": n.title,
                "description": n.description,
                "status": n.status.value,
                "depth": n.depth,
                "clarification_q": n.clarification_question,
                "clarification_a": n.clarification_answer,
                "bound_step": n.bound_step_id,
                "abandon_reason": n.abandon_reason,
            }
            for n in _ordered(snapshot.nodes)
        ],
    }


def _ordered(nodes: Iterable[TodoNode]) -> list[TodoNode]:
    return sorted(nodes, key=lambda n: (n.depth, n.sequence, n.node_id))
