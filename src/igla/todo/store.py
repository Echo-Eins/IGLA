"""Persistence for ``TodoTree``.

Per-task JSON snapshot at ``.igla/todo/<task_id>.json``. Snapshots are
written on every committed mutation; the file is replaced atomically via
the ``write to tmp -> rename`` pattern.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..protocol.todo import TodoNode, TodoTreeSnapshot
from .tree import TodoTree


class TodoStore:
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def path_for(self, task_id: str) -> Path:
        return self._base / f"{task_id}.json"

    def save(self, tree: TodoTree) -> Path:
        snapshot = tree.snapshot()
        path = self.path_for(tree.task_id)
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, path)
        return path

    def load_snapshot(self, task_id: str) -> TodoTreeSnapshot | None:
        path = self.path_for(task_id)
        if not path.exists():
            return None
        return TodoTreeSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def all_task_ids(self) -> list[str]:
        return sorted(p.stem for p in self._base.glob("*.json"))

    @staticmethod
    def export_nodes(snapshot: TodoTreeSnapshot) -> list[dict[str, object]]:
        return [json.loads(node.model_dump_json()) for node in snapshot.nodes]

    @staticmethod
    def nodes_dict(nodes: list[TodoNode]) -> dict[str, TodoNode]:
        return {n.node_id: n for n in nodes}
