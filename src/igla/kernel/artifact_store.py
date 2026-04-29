"""Artifact store.

Implementation notes:
* Each artifact is a directory ``<artifacts_dir>/<artifact_id>/`` with
  ``descriptor.json`` and either ``content.json`` (inline) or a copy of
  the referenced file at ``payload``. We avoid touching the original
  source path so that the store is fully self-contained.
* Content hashes are SHA-256 of the canonical body. Tools may pass an
  inline ``dict`` or a file path; the store computes the hash either way.
* Once written, a descriptor is immutable. To replace an artifact we
  emit a new one with parents referencing the old.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any

from ..ids import prefixed_id
from ..protocol.artifact import (
    ArtifactCreateRequest,
    ArtifactDescriptor,
)
from .clock import Clock
from .errors import ArtifactStoreError


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


class ArtifactStore:
    def __init__(self, base_dir: Path, clock: Clock) -> None:
        self._base = base_dir
        self._clock = clock
        self._lock = threading.Lock()
        self._base.mkdir(parents=True, exist_ok=True)

    def put(self, request: ArtifactCreateRequest) -> ArtifactDescriptor:
        if (request.inline_content is None) == (request.location is None):
            raise ArtifactStoreError(
                "ArtifactCreateRequest must specify exactly one of inline_content / location"
            )

        artifact_id = prefixed_id("art")
        artifact_dir = self._base / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=False)

        location: str
        content_hash: str

        if request.inline_content is not None:
            data = _canonical_json(request.inline_content)
            content_hash = _sha256_bytes(data)
            target = artifact_dir / "content.json"
            target.write_bytes(data)
            location = str(target)
        else:
            assert request.location is not None
            src = Path(request.location).resolve()
            if not src.exists():
                raise ArtifactStoreError(f"location does not exist: {src}")
            content_hash = _sha256_file(src)
            target = artifact_dir / "payload"
            shutil.copy2(src, target)
            location = str(target)

        descriptor = ArtifactDescriptor(
            artifact_id=artifact_id,
            artifact_type=request.artifact_type,
            schema_version=request.schema_version,
            producer_tool=request.producer_tool,
            producer_version=request.producer_version,
            producer_invocation_id=request.producer_invocation_id,
            location=location,
            content_hash=content_hash,
            summary=request.summary,
            metadata=request.metadata,
            parents=request.parents,
            created_at=self._clock.now(),
        )
        with self._lock:
            (artifact_dir / "descriptor.json").write_text(
                descriptor.model_dump_json(indent=2), encoding="utf-8"
            )
        return descriptor

    def get(self, artifact_id: str) -> ArtifactDescriptor:
        descriptor_path = self._base / artifact_id / "descriptor.json"
        if not descriptor_path.exists():
            raise ArtifactStoreError(f"artifact not found: {artifact_id}")
        return ArtifactDescriptor.model_validate_json(
            descriptor_path.read_text(encoding="utf-8")
        )

    def open_bytes(self, artifact_id: str) -> bytes:
        descriptor = self.get(artifact_id)
        if descriptor.location is None:
            raise ArtifactStoreError(f"artifact has no location: {artifact_id}")
        return Path(descriptor.location).read_bytes()

    def derive(
        self,
        parent_ids: list[str],
        request: ArtifactCreateRequest,
    ) -> ArtifactDescriptor:
        merged_parents = list(dict.fromkeys([*parent_ids, *request.parents]))
        # Pydantic v2 frozen models support model_copy(update=...) which
        # constructs a new instance.
        derived_request = request.model_copy(update={"parents": merged_parents})
        return self.put(derived_request)
