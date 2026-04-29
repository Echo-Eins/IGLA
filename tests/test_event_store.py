"""EventStore tests."""
from __future__ import annotations

from datetime import datetime, timezone

from igla.kernel.clock import StepClock
from igla.kernel.event_store import EventStore
from igla.protocol import EventKind


def test_append_and_iterate(tmp_path) -> None:
    clock = StepClock(start=datetime(2026, 4, 29, tzinfo=timezone.utc), step_seconds=0.5)
    store = EventStore(tmp_path / "events.jsonl", clock)
    a = store.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    b = store.append(kind=EventKind.STEP_STARTED, actor="planner", task_id="t1", step_id="s1")
    c = store.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t2")

    events = list(store.iter_all())
    assert [e.event_id for e in events] == [a.event_id, b.event_id, c.event_id]
    assert [e.task_id for e in store.list_by_task("t1")] == ["t1", "t1"]


def test_iter_tolerates_blank_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    clock = StepClock(start=datetime(2026, 4, 29, tzinfo=timezone.utc))
    store = EventStore(path, clock)
    store.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    # Append a torn line — must not crash readers.
    with path.open("a", encoding="utf-8") as fp:
        fp.write("\n   \n")
        fp.write("not json\n")
    events = list(store.iter_all())
    assert len(events) == 1


def test_filter(tmp_path) -> None:
    clock = StepClock(start=datetime(2026, 4, 29, tzinfo=timezone.utc))
    store = EventStore(tmp_path / "events.jsonl", clock)
    store.append(kind=EventKind.TASK_CREATED, actor="runtime", task_id="t1")
    store.append(kind=EventKind.STEP_FAILED, actor="runtime", task_id="t1", step_id="s1")
    store.append(kind=EventKind.STEP_FAILED, actor="runtime", task_id="t2", step_id="s2")

    failures = store.filter(kinds=[EventKind.STEP_FAILED])
    assert len(failures) == 2
    only_t1 = store.filter(kinds=[EventKind.STEP_FAILED], task_id="t1")
    assert len(only_t1) == 1
