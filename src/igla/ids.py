"""ID generation.

ULIDs (Crockford base32, lexicographically sortable, 128 bits) are used for
all runtime identifiers. We avoid an external dependency and generate them
from os.urandom + millisecond timestamp.
"""
from __future__ import annotations

import os
import time
from typing import Final

_CROCKFORD: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """Return a Crockford base32 ULID (26 chars)."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = int.from_bytes(os.urandom(10), "big")
    return _encode(ts_ms, 10) + _encode(rnd, 16)


def prefixed_id(prefix: str) -> str:
    """Return ``"<prefix>_<ulid>"``. Prefixes: task, step, inv, art, ev, evt, rcp, mem, todo, prop."""
    return f"{prefix}_{ulid()}"
