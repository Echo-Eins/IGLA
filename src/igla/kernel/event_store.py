"""Append-only event store backed by JSONL.

Design choices:
* JSONL keeps the store debuggable with ``cat``/``less``; every line is a
  fully self-describing ``EventRecord``.
* Writes are atomic per line: we open in ``a`` mode and write one
  ``json.dumps`` blob followed by ``\\n``; readers handle truncated tails.
* No in-memory cache. ``list_by_task`` walks the file. This is fine for
  tens of thousands of events; replace with SQLite when it stops being so.
* The store is process-safe under Linux because POSIX guarantees that
  ``write(2)`` calls smaller than ``PIPE_BUF`` are atomic for files opened
  with O_APPEND, and a JSON line of <4 KiB falls comfortably under that.

The file is created lazily on the first ``append``.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ..ids import prefixed_id
from ..protocol.event import EventKind, EventRecord
from .clock import Clock


def _serialise(event: EventRecord) -> str:
    return event.model_dump_json(by_alias=False)


def _deserialise(line: str) -> EventRecord:
    return EventRecord.model_validate_json(line)


class EventStore:
    """Append-only event log. Thread-safe within one process."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        kind: EventKind,
        actor: str,
        task_id: str | None = None,
        step_id: str | None = None,
        payload: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> EventRecord:
        record = EventRecord(
            event_id=prefixed_id("evt"),
            kind=kind,
            actor=actor,
            task_id=task_id,
            step_id=step_id,
            payload=payload or {},
            timestamp=timestamp or self._clock.now(),
        )
        line = _serialise(record)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(line)
                fp.write("\n")
        return record

    # --- read API -------------------------------------------------------

    def iter_all(self) -> Iterator[EventRecord]:
        if not self._path.exists():
            return iter(())
        return self._iter_file()

    def _iter_file(self) -> Iterator[EventRecord]:
        with self._path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    yield _deserialise(line)
                except Exception:
                    # tolerate a torn last line at the very tail; never raise
                    continue

    def list_by_task(self, task_id: str) -> list[EventRecord]:
        return [event for event in self.iter_all() if event.task_id == task_id]

    def filter(
        self,
        *,
        kinds: Iterable[EventKind] | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> list[EventRecord]:
        kinds_set = set(kinds) if kinds else None
        out: list[EventRecord] = []
        for event in self.iter_all():
            if kinds_set is not None and event.kind not in kinds_set:
                continue
            if task_id is not None and event.task_id != task_id:
                continue
            if step_id is not None and event.step_id != step_id:
                continue
            out.append(event)
        return out
