"""Read receipts.

Required by ``read_before_write`` and ``hash_before_patch`` invariants. Even
though the file-system tools are not implemented in this MVP, the receipt
type is part of the frozen protocol surface so that later layers do not have
to retrofit it.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class ReceiptKind(str, Enum):
    FILE_READ = "file_read"
    DIR_LISTING = "dir_listing"
    INTAKE = "intake"


class FileReadReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str
    kind: ReceiptKind = ReceiptKind.FILE_READ

    path: str
    sha256: str
    """SHA-256 of the content slice returned by ``read_file``."""
    file_sha256: str | None = None
    """SHA-256 of the full file at read time. Used by ``patch_file``'s
    ``read_before_write`` and ``hash_matches_receipt`` invariants."""
    bytes_read: int
    read_mode: str = "full"

    task_id: str
    step_id: str | None = None
    timestamp: datetime
