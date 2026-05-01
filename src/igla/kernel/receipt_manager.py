"""Receipt manager — read receipts and intake markers.

In MVP-0 we keep the receipts in process memory only (per-task dict). The
``IntakeMarker`` for ``todo_before_execution`` is also tracked here so that
later policy predicates can ask "did intake happen for this task?" without
walking the event log.

Receipts get persisted as a JSON snapshot per task on demand via ``snapshot``
so that the chat REPL can resume a session.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..ids import prefixed_id
from ..protocol.receipt import FileReadReceipt, ReceiptKind
from .clock import Clock


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class ReceiptManager:
    def __init__(self, base_dir: Path, clock: Clock) -> None:
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._file_receipts: dict[tuple[str, str], FileReadReceipt] = {}
        self._intake: set[str] = set()
        self._lock = threading.Lock()

    # --- file read receipts --------------------------------------------

    def record_file_read(
        self,
        *,
        task_id: str,
        path: str,
        content: str,
        bytes_read: int,
        step_id: str | None = None,
        file_sha256: str | None = None,
    ) -> FileReadReceipt:
        """Record a read receipt.

        ``file_sha256`` is the SHA-256 of the *entire file on disk* at the
        time of reading, irrespective of any partial-range slice the caller
        may have requested. ``patch_file``'s ``hash_matches_receipt``
        predicate relies on this value to detect stale writes.
        """
        receipt = FileReadReceipt(
            receipt_id=prefixed_id("rcp"),
            kind=ReceiptKind.FILE_READ,
            path=path,
            sha256=_hash_text(content),
            file_sha256=file_sha256,
            bytes_read=bytes_read,
            task_id=task_id,
            step_id=step_id,
            timestamp=self._clock.now(),
        )
        with self._lock:
            self._file_receipts[(task_id, path)] = receipt
        return receipt

    def get_file_read(self, task_id: str, path: str) -> FileReadReceipt | None:
        return self._file_receipts.get((task_id, path))

    # --- intake markers -------------------------------------------------

    def mark_intake(self, task_id: str) -> None:
        with self._lock:
            self._intake.add(task_id)

    def has_intake(self, task_id: str) -> bool:
        return task_id in self._intake

    # --- persistence ----------------------------------------------------

    def snapshot(self, task_id: str) -> dict[str, object]:
        return {
            "task_id": task_id,
            "intake": task_id in self._intake,
            "file_receipts": {
                path: receipt.model_dump(mode="json")
                for (tid, path), receipt in self._file_receipts.items()
                if tid == task_id
            },
        }
