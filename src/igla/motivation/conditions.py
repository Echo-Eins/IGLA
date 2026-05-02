"""Condition functions for motivation rules.

Conditions decide whether a rule's effects fire after its trigger matched.
They are kept side-effect free; mutations live in ``effects.py``.

Adding a new condition:

```python
def my_condition(ctx: ConditionContext) -> bool: ...
register_condition("my_condition", my_condition)
```
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..protocol.event import EventRecord
from ..protocol.runtime import RuntimeMode


@dataclass
class ConditionContext:
    """Read-only context for evaluating a rule condition."""

    event: EventRecord
    task_state: Any  # TaskRuntimeState; typed loosely to avoid circular import
    kernel: Any
    rule_args: dict[str, Any]


ConditionFn = Callable[[ConditionContext], bool]
CONDITIONS: dict[str, ConditionFn] = {}


def register_condition(name: str, fn: ConditionFn) -> None:
    if name in CONDITIONS:
        raise ValueError(f"condition already registered: {name}")
    CONDITIONS[name] = fn


# ---------------------------------------------------------------------------


def _always(ctx: ConditionContext) -> bool:
    return True


def _runtime_mode_is(ctx: ConditionContext) -> bool:
    expected = ctx.rule_args.get("mode")
    if not expected:
        raise ValueError("runtime_mode_is requires args.mode")
    return bool(ctx.task_state.mode == RuntimeMode(expected))


def _runtime_mode_in(ctx: ConditionContext) -> bool:
    modes = ctx.rule_args.get("modes") or []
    target = {RuntimeMode(m) for m in modes}
    return ctx.task_state.mode in target


def _flag_set(ctx: ConditionContext) -> bool:
    flag = ctx.rule_args.get("flag")
    if not flag:
        raise ValueError("flag_set requires args.flag")
    return bool(getattr(ctx.task_state, flag, False))


def _flag_not_set(ctx: ConditionContext) -> bool:
    return not _flag_set(ctx)


def _all_flags_set(ctx: ConditionContext) -> bool:
    flags = ctx.rule_args.get("flags") or []
    return all(bool(getattr(ctx.task_state, f, False)) for f in flags)


def _payload_equals(ctx: ConditionContext) -> bool:
    """Match a key inside ``event.payload`` against ``args.value``.

    Uses dotted-path: ``args.path = "step.status"`` reads
    ``event.payload["step"]["status"]``.
    """
    path = ctx.rule_args.get("path")
    if not path:
        raise ValueError("payload_equals requires args.path")
    expected = ctx.rule_args.get("value")
    actual: Any = ctx.event.payload
    for part in path.split("."):
        if not isinstance(actual, dict) or part not in actual:
            return False
        actual = actual[part]
    return bool(actual == expected)


def _payload_in(ctx: ConditionContext) -> bool:
    path = ctx.rule_args.get("path")
    values = ctx.rule_args.get("values") or []
    if not path:
        raise ValueError("payload_in requires args.path")
    actual: Any = ctx.event.payload
    for part in path.split("."):
        if not isinstance(actual, dict) or part not in actual:
            return False
        actual = actual[part]
    return bool(actual in values)


def _tool_called_was(ctx: ConditionContext) -> bool:
    """Match the tool name on tool_invocation_completed/failed events."""
    expected = ctx.rule_args.get("name")
    if not expected:
        raise ValueError("tool_called_was requires args.name")
    return bool(ctx.event.payload.get("tool_name") == expected)


def _result_status_was(ctx: ConditionContext) -> bool:
    expected = ctx.rule_args.get("status")
    if not expected:
        raise ValueError("result_status_was requires args.status")
    return bool(ctx.event.payload.get("status") == expected)


def _last_error_code_in(ctx: ConditionContext) -> bool:
    codes = set(ctx.rule_args.get("codes") or [])
    if not codes:
        raise ValueError("last_error_code_in requires args.codes")
    return ctx.task_state.last_error_code in codes


for _name, _fn in [
    ("always", _always),
    ("runtime_mode_is", _runtime_mode_is),
    ("runtime_mode_in", _runtime_mode_in),
    ("flag_set", _flag_set),
    ("flag_not_set", _flag_not_set),
    ("all_flags_set", _all_flags_set),
    ("payload_equals", _payload_equals),
    ("payload_in", _payload_in),
    ("tool_called_was", _tool_called_was),
    ("result_status_was", _result_status_was),
    ("last_error_code_in", _last_error_code_in),
]:
    register_condition(_name, _fn)
