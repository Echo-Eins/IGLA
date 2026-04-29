"""Evidence store — JSONL backed."""
from __future__ import annotations

import threading
from pathlib import Path

from ..ids import prefixed_id
from ..protocol.evidence import EvidenceCreateRequest, EvidenceRecord
from .clock import Clock


class EvidenceStore:
    def __init__(self, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def create(self, request: EvidenceCreateRequest) -> EvidenceRecord:
        record = EvidenceRecord(
            evidence_id=prefixed_id("ev"),
            kind=request.kind,
            task_id=request.task_id,
            step_id=request.step_id,
            source_event_id=request.source_event_id,
            artifact_id=request.artifact_id,
            summary=request.summary,
            location=request.location,
            content_hash=request.content_hash,
            confidence=request.confidence,
            metadata=request.metadata,
            created_at=self._clock.now(),
        )
        line = record.model_dump_json()
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fp:
                fp.write(line)
                fp.write("\n")
        return record

    def list_by_task(self, task_id: str) -> list[EvidenceRecord]:
        if not self._path.exists():
            return []
        out: list[EvidenceRecord] = []
        with self._path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = EvidenceRecord.model_validate_json(line)
                except Exception:
                    continue
                if record.task_id == task_id:
                    out.append(record)
        return out

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        if not self._path.exists():
            return None
        with self._path.open("r", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = EvidenceRecord.model_validate_json(line)
                except Exception:
                    continue
                if record.evidence_id == evidence_id:
                    return record
        return None
