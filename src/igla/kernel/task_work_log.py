"""Task work log — compact, per-task journal of model actions.

This is the first slab of the *memory* subsystem.  Long-term memory
(``facts.jsonl`` + ``cases.jsonl``, dedup, decay, system-info accretion)
lives downstream; the work log is the per-task primary source.

Design choices (MVP):

* The work log is **derived from the EventStore**, not a separate write
  channel.  EventStore already records every tool invocation, every
  policy decision, every motivation effect; duplicating those writes
  would risk drift between the audit log and the work log.  Instead we
  expose a *filtered, compact view* — kept small so the model can read
  its own history without blowing the context window.
* The view is cheap to recompute (one walk per call).  When task counts
  exceed thousands of events we will swap the EventStore for SQLite or
  index the work log incrementally; the public surface stays the same.
* The per-task ``close`` contract is satisfied by the existing
  ``task_completed`` event.  When that event has been recorded for a
  task, the log is "closed" — :meth:`is_closed` returns ``True`` and the
  ``read_task_log`` tool surfaces the closure to the model.

The shape of one log entry is intentionally *not* a Pydantic model;
keeping it as a plain ``dict`` avoids version coupling between the
in-process feeder and the JSON output schema of the ``read_task_log``
tool.
"""
from __future__ import annotations

from typing import Any

from ..protocol.event import EventKind, EventRecord
from .event_store import EventStore

# Event kinds that materially affect the model's view of its own work.
# Discovery/structural events (todo branches, runtime mode flips) live on
# the event log but are noise for memory purposes.
_WORK_EVENT_KINDS: frozenset[EventKind] = frozenset({
    EventKind.TASK_CREATED,
    EventKind.TOOL_INVOCATION_STARTED,
    EventKind.TOOL_INVOCATION_COMPLETED,
    EventKind.TOOL_INVOCATION_FAILED,
    EventKind.POLICY_REJECTION,
    EventKind.USER_INPUT_RECEIVED,
    EventKind.TASK_COMPLETED,
    EventKind.TASK_FAILED,
})


class TaskWorkLog:
    """Read-only, EventStore-backed work journal."""

    def __init__(self, events: EventStore) -> None:
        self._events = events

    # ---- queries -------------------------------------------------------- #

    def get_entries(
        self,
        task_id: str,
        *,
        max_entries: int | None = None,
    ) -> list[dict[str, Any]]:
        """Compact list of work-relevant events for ``task_id``.

        ``max_entries`` truncates from the *front*: the most recent
        entries always survive, because they are what the model needs to
        reason about its next move.  ``None`` means no truncation.
        """
        raw = self._events.list_by_task(task_id)
        kept = [e for e in raw if e.kind in _WORK_EVENT_KINDS]
        if max_entries is not None and len(kept) > max_entries:
            kept = kept[-max_entries:]
        out: list[dict[str, Any]] = []
        for seq, event in enumerate(kept, start=1):
            out.append(_summarise_event(event, seq=seq))
        return out

    def is_closed(self, task_id: str) -> bool:
        """True iff a ``task_completed`` or ``task_failed`` event exists."""
        for event in self._events.list_by_task(task_id):
            if event.kind in (EventKind.TASK_COMPLETED, EventKind.TASK_FAILED):
                return True
        return False

    def get_summary(self, task_id: str) -> dict[str, Any]:
        """High-level rollup: counts, status, last-seen tools."""
        raw = self._events.list_by_task(task_id)
        tool_calls = 0
        tool_failures = 0
        rejections = 0
        last_tool: str | None = None
        files_touched: set[str] = set()
        goal: str | None = None
        is_done = False
        is_failed = False
        done_summary: str | None = None

        for event in raw:
            if event.kind is EventKind.TASK_CREATED:
                goal = str(event.payload.get("goal") or "") or None
            elif event.kind is EventKind.TOOL_INVOCATION_COMPLETED:
                tool_calls += 1
                last_tool = str(event.payload.get("tool_name") or "")
                _collect_file_paths(event.payload, files_touched)
            elif event.kind is EventKind.TOOL_INVOCATION_FAILED:
                tool_calls += 1
                tool_failures += 1
                last_tool = str(event.payload.get("tool_name") or "")
            elif event.kind is EventKind.POLICY_REJECTION:
                rejections += 1
            elif event.kind is EventKind.TASK_COMPLETED:
                is_done = True
                done_summary = str(event.payload.get("summary") or "") or None
            elif event.kind is EventKind.TASK_FAILED:
                is_failed = True
                done_summary = str(event.payload.get("reason") or "") or None

        return {
            "task_id": task_id,
            "goal": goal,
            "tool_calls": tool_calls,
            "tool_failures": tool_failures,
            "policy_rejections": rejections,
            "last_tool": last_tool,
            "files_touched": sorted(files_touched),
            "closed": is_done or is_failed,
            "status": (
                "done" if is_done else "failed" if is_failed else "in_progress"
            ),
            "summary": done_summary,
        }


# ---------------------------------------------------------------------------#
# Compact rendering                                                         #
# ---------------------------------------------------------------------------#


def _summarise_event(event: EventRecord, *, seq: int) -> dict[str, Any]:
    """Convert an EventRecord to the work-log entry shape.

    Keep the dicts small — they will end up inside a planner prompt.
    """
    base: dict[str, Any] = {
        "seq": seq,
        "event_id": event.event_id,
        "kind": event.kind.value,
        "actor": event.actor,
        "timestamp": event.timestamp.isoformat(),
    }
    if event.step_id:
        base["step_id"] = event.step_id

    payload = event.payload or {}

    if event.kind is EventKind.TASK_CREATED:
        base["goal"] = str(payload.get("goal") or "")[:200]
        base["raw_request"] = str(payload.get("raw_request") or "")[:200]
        return base

    if event.kind is EventKind.TOOL_INVOCATION_STARTED:
        base["tool"] = payload.get("tool_name")
        if payload.get("reason"):
            base["reason"] = str(payload["reason"])[:200]
        return base

    if event.kind in (
        EventKind.TOOL_INVOCATION_COMPLETED,
        EventKind.TOOL_INVOCATION_FAILED,
    ):
        base["tool"] = payload.get("tool_name")
        base["status"] = payload.get("status")
        if payload.get("error_code"):
            base["error_code"] = payload["error_code"]
        if payload.get("output_keys"):
            base["output_keys"] = list(payload["output_keys"])[:12]
        compact_out = payload.get("output")
        if isinstance(compact_out, dict):
            base["output_summary"] = _summarise_tool_output(
                str(payload.get("tool_name") or ""), compact_out
            )
        return base

    if event.kind is EventKind.POLICY_REJECTION:
        base["reason_code"] = payload.get("reason_code")
        msg = payload.get("message")
        if msg:
            base["message"] = str(msg)[:200]
        if payload.get("tool_name"):
            base["tool"] = payload["tool_name"]
        return base

    if event.kind is EventKind.USER_INPUT_RECEIVED:
        text = payload.get("text", "")
        base["text"] = str(text)[:200]
        return base

    if event.kind is EventKind.TASK_COMPLETED:
        if payload.get("summary"):
            base["summary"] = str(payload["summary"])[:300]
        if payload.get("reason"):
            base["reason"] = str(payload["reason"])[:200]
        return base

    if event.kind is EventKind.TASK_FAILED:
        if payload.get("reason"):
            base["reason"] = str(payload["reason"])[:200]
        return base

    return base


def _summarise_tool_output(tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
    """Trim per-tool outputs to the few fields the model actually re-uses.

    Bigger payloads (file content, ruff diffs) stay in the original
    completion event; the work log keeps just enough to reconstruct
    "what did I do".
    """
    name = tool_name.lower()
    if name == "copy_file":
        return _pick(
            output,
            [
                "source_path",
                "destination_path",
                "bytes_written",
                "appended_bytes",
                "sha256_after",
                "overwrote",
            ],
        )
    if name == "patch_file":
        return _pick(output, ["path", "patch_mode", "occurrences_replaced", "sha256_after"])
    if name == "restore_file":
        return _pick(output, ["path", "backup_artifact_id"])
    if name == "verify_file":
        return _pick(
            output,
            ["path", "file_type", "checks_run", "overall_passed"],
        )
    if name == "find_files":
        matches = output.get("matches")
        if isinstance(matches, list):
            return {
                "count": output.get("count"),
                "first_paths": [
                    str(r.get("relative_path") if isinstance(r, dict) else r)
                    for r in matches[:5]
                ],
            }
        return _pick(output, ["count"])
    if name == "list_dir":
        return _pick(output, ["root", "total_files", "total_dirs"])
    if name == "read_file":
        return _pick(
            output,
            ["path", "start_line", "end_line", "total_lines", "end_of_file"],
        )
    if name == "search_text":
        matches = output.get("matches")
        if isinstance(matches, list):
            return {
                "count": output.get("count"),
                "first_matches": [
                    {
                        "relative_path": r.get("relative_path"),
                        "line_number": r.get("line_number"),
                    }
                    for r in matches[:5]
                    if isinstance(r, dict)
                ],
            }
        return _pick(output, ["count"])
    return {}


def _pick(d: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {k: d[k] for k in keys if k in d}


def _collect_file_paths(payload: dict[str, Any], sink: set[str]) -> None:
    """Best-effort extraction of file paths a tool touched."""
    out = payload.get("output")
    if not isinstance(out, dict):
        return
    name = str(payload.get("tool_name") or "").lower()
    if name in ("patch_file", "restore_file", "verify_file", "read_file"):
        path = out.get("path")
        if isinstance(path, str):
            sink.add(path)
    if name == "copy_file":
        for key in ("source_path", "destination_path"):
            path = out.get(key)
            if isinstance(path, str):
                sink.add(path)
