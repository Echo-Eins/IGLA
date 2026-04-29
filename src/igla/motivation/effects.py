"""Effect functions for motivation rules.

Effects are the **only** way motivation rules mutate state. They receive a
``EffectContext`` carrying the kernel handle, the task state, and the
event that triggered the rule.

Adding a new effect:

```python
def my_effect(ctx: EffectContext) -> None: ...
register_effect("my_effect", my_effect)
```
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..protocol.event import EventKind, EventRecord
from ..protocol.runtime import RuntimeMode


@dataclass
class EffectContext:
    event: EventRecord
    task_state: Any  # TaskRuntimeState
    kernel: Any
    rule_id: str
    rule_args: dict[str, Any]


EffectFn = Callable[[EffectContext], None]
EFFECTS: dict[str, EffectFn] = {}


def register_effect(name: str, fn: EffectFn) -> None:
    if name in EFFECTS:
        raise ValueError(f"effect already registered: {name}")
    EFFECTS[name] = fn


# ---------------------------------------------------------------------------


def _set_mode(ctx: EffectContext) -> None:
    target = ctx.rule_args.get("mode")
    if not target:
        raise ValueError("set_mode requires args.mode")
    target_mode = RuntimeMode(target)
    if ctx.task_state.mode == target_mode:
        return
    ctx.kernel.state.transition_mode(ctx.task_state.task_id, target_mode)
    ctx.kernel.events.append(
        kind=EventKind.RUNTIME_MODE_CHANGED,
        actor="motivation",
        task_id=ctx.task_state.task_id,
        payload={"new_mode": target_mode.value, "rule": ctx.rule_id},
    )


def _set_allowed_actions(ctx: EffectContext) -> None:
    actions = list(ctx.rule_args.get("actions") or [])
    ctx.task_state.allowed_next_actions = actions


def _set_forbidden_actions(ctx: EffectContext) -> None:
    actions = list(ctx.rule_args.get("actions") or [])
    ctx.task_state.forbidden_next_actions = actions


def _add_allowed_actions(ctx: EffectContext) -> None:
    actions = list(ctx.rule_args.get("actions") or [])
    existing = list(ctx.task_state.allowed_next_actions)
    for action in actions:
        if action not in existing:
            existing.append(action)
    ctx.task_state.allowed_next_actions = existing


def _add_forbidden_actions(ctx: EffectContext) -> None:
    actions = list(ctx.rule_args.get("actions") or [])
    existing = list(ctx.task_state.forbidden_next_actions)
    for action in actions:
        if action not in existing:
            existing.append(action)
    ctx.task_state.forbidden_next_actions = existing


def _clear_allowed_actions(ctx: EffectContext) -> None:
    ctx.task_state.allowed_next_actions = []


def _clear_forbidden_actions(ctx: EffectContext) -> None:
    ctx.task_state.forbidden_next_actions = []


def _set_flag(ctx: EffectContext) -> None:
    flag = ctx.rule_args.get("flag")
    if not flag:
        raise ValueError("set_flag requires args.flag")
    value = bool(ctx.rule_args.get("value", True))
    if not hasattr(ctx.task_state, flag):
        raise ValueError(f"set_flag: TaskRuntimeState has no flag {flag!r}")
    setattr(ctx.task_state, flag, value)


def _clear_flags(ctx: EffectContext) -> None:
    flags = list(ctx.rule_args.get("flags") or [])
    for flag in flags:
        if hasattr(ctx.task_state, flag):
            setattr(ctx.task_state, flag, False)


def _log_message(ctx: EffectContext) -> None:
    message = str(ctx.rule_args.get("message", ""))
    ctx.kernel.events.append(
        kind=EventKind.SYSTEM_MESSAGE,
        actor="motivation",
        task_id=ctx.task_state.task_id,
        payload={"rule": ctx.rule_id, "message": message},
    )


def _pause_for_clarification(ctx: EffectContext) -> None:
    """Capture the current mode + action lists so they can be restored on resume.

    This makes ``NEEDS_USER_CLARIFICATION`` a transparent overlay rather
    than a state that erases prior diagnosis context. After this runs the
    runtime is in NEEDS_USER_CLARIFICATION with an empty allowed-actions
    list.
    """
    state = ctx.task_state
    if state.mode is RuntimeMode.NEEDS_USER_CLARIFICATION:
        return  # already paused; do not stack
    state.pre_pause_mode = state.mode
    state.pre_pause_allowed = list(state.allowed_next_actions)
    state.pre_pause_forbidden = list(state.forbidden_next_actions)
    ctx.kernel.state.transition_mode(state.task_id, RuntimeMode.NEEDS_USER_CLARIFICATION)
    state.allowed_next_actions = []
    state.forbidden_next_actions = []
    ctx.kernel.events.append(
        kind=EventKind.RUNTIME_MODE_CHANGED,
        actor="motivation",
        task_id=state.task_id,
        payload={
            "new_mode": RuntimeMode.NEEDS_USER_CLARIFICATION.value,
            "rule": ctx.rule_id,
            "paused_from": state.pre_pause_mode.value if state.pre_pause_mode else None,
        },
    )


def _resume_from_clarification(ctx: EffectContext) -> None:
    """Restore the pre-pause state after a user reply.

    If we were in FAILURE_DIAGNOSIS_REQUIRED before pausing we go back
    there. Otherwise we land in READY.
    """
    state = ctx.task_state
    if state.mode is not RuntimeMode.NEEDS_USER_CLARIFICATION:
        return
    target = state.pre_pause_mode or RuntimeMode.READY
    ctx.kernel.state.transition_mode(state.task_id, target)
    state.allowed_next_actions = list(state.pre_pause_allowed)
    state.forbidden_next_actions = list(state.pre_pause_forbidden)
    state.pre_pause_mode = None
    state.pre_pause_allowed = []
    state.pre_pause_forbidden = []
    ctx.kernel.events.append(
        kind=EventKind.RUNTIME_MODE_CHANGED,
        actor="motivation",
        task_id=state.task_id,
        payload={"new_mode": target.value, "rule": ctx.rule_id, "resumed": True},
    )


for _name, _fn in [
    ("set_mode", _set_mode),
    ("set_allowed_actions", _set_allowed_actions),
    ("set_forbidden_actions", _set_forbidden_actions),
    ("add_allowed_actions", _add_allowed_actions),
    ("add_forbidden_actions", _add_forbidden_actions),
    ("clear_allowed_actions", _clear_allowed_actions),
    ("clear_forbidden_actions", _clear_forbidden_actions),
    ("set_flag", _set_flag),
    ("clear_flags", _clear_flags),
    ("log_message", _log_message),
    ("pause_for_clarification", _pause_for_clarification),
    ("resume_from_clarification", _resume_from_clarification),
]:
    register_effect(_name, _fn)
