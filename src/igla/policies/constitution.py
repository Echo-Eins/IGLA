"""Loader for ``constitution.yaml``.

The constitution is a flat list of predicate references with optional
overrides. The loader returns an in-memory list which the policy engine
iterates on every check.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .predicates import PREDICATES, PredicateFn


@dataclass(frozen=True)
class ConstitutionEntry:
    id: str
    enabled: bool
    rule_id: str
    options: dict[str, Any]
    predicate: PredicateFn


@dataclass(frozen=True)
class Constitution:
    version: int
    entries: tuple[ConstitutionEntry, ...]

    def enabled(self) -> tuple[ConstitutionEntry, ...]:
        return tuple(entry for entry in self.entries if entry.enabled)


def load_constitution(path: Path) -> Constitution:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"constitution must be a mapping at root: {path}")
    version = int(raw.get("version", 1))
    raw_entries = raw.get("predicates") or []
    entries: list[ConstitutionEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise ValueError(f"constitution entry must be a mapping: {item!r}")
        ident = str(item["id"])
        enabled = bool(item.get("enabled", True))
        rule_id = str(item.get("rule_id", ident))
        options = dict(item.get("options") or {})
        predicate = PREDICATES.get(ident)
        if predicate is None:
            raise ValueError(f"unknown constitution predicate: {ident}")
        entries.append(
            ConstitutionEntry(
                id=ident,
                enabled=enabled,
                rule_id=rule_id,
                options=options,
                predicate=predicate,
            )
        )
    return Constitution(version=version, entries=tuple(entries))
