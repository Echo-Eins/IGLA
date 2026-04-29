"""Declarative motivation rule model + YAML loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class TriggerSpec(BaseModel):
    """One trigger entry. A rule fires if **any** of its triggers match."""

    model_config = ConfigDict(extra="forbid")

    on_event: str | None = None  # EventKind value, e.g. "step_failed"
    on_action_kind: str | None = None  # for proposal-time triggers
    always: bool = False  # if true, fires on every dispatch


class RuleConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)


class EffectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)


class MotivationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str | None = None
    enabled: bool = True
    priority: int = 100  # lower runs first
    triggers: list[TriggerSpec] = Field(default_factory=list)
    conditions: list[RuleConditionSpec] = Field(default_factory=list)
    effects: list[EffectSpec] = Field(default_factory=list)


def load_rules(path: Path) -> list[MotivationRule]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"motivation rules file must be a mapping at root: {path}")
    rules_raw = raw.get("rules") or []
    rules: list[MotivationRule] = [MotivationRule.model_validate(r) for r in rules_raw]
    rules.sort(key=lambda r: (r.priority, r.id))
    return rules
