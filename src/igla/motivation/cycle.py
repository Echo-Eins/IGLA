"""The motivation dispatch cycle.

After every event lands in ``EventStore``, the kernel calls
``MotivationCycle.dispatch`` to give rules a chance to react.

Dispatch is **synchronous** and runs to completion before the next event is
appended; this keeps reasoning about "what state was in effect when the
planner saw it" simple.

Reentry: a rule effect that emits its own event (e.g. ``log_message``) does
not re-enter the dispatch loop with that event — only events emitted from
*outside* motivation (planner/executor/runtime) are dispatched. This avoids
infinite cascades.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..protocol.event import EventKind, EventRecord
from .conditions import CONDITIONS, ConditionContext
from .effects import EFFECTS, EffectContext
from .rule import MotivationRule, TriggerSpec


@dataclass
class DispatchResult:
    matched_rule_ids: list[str]
    applied_rule_ids: list[str]


class MotivationCycle:
    def __init__(self, rules: Iterable[MotivationRule], kernel) -> None:  # noqa: ANN001
        self._rules = sorted(
            (r for r in rules if r.enabled),
            key=lambda r: (r.priority, r.id),
        )
        self._kernel = kernel

    @property
    def rules(self) -> list[MotivationRule]:
        return list(self._rules)

    def dispatch(self, event: EventRecord) -> DispatchResult:
        if event.task_id is None:
            return DispatchResult([], [])
        task_state = self._kernel.state.ensure_task(event.task_id)

        matched: list[str] = []
        applied: list[str] = []

        for rule in self._rules:
            if not _match_triggers(event, rule.triggers):
                continue
            matched.append(rule.id)

            if not _check_conditions(event, rule, task_state, self._kernel):
                continue

            applied.append(rule.id)
            for effect in rule.effects:
                fn = EFFECTS.get(effect.kind)
                if fn is None:
                    raise ValueError(f"unknown effect kind: {effect.kind}")
                ctx = EffectContext(
                    event=event,
                    task_state=task_state,
                    kernel=self._kernel,
                    rule_id=rule.id,
                    rule_args=effect.args,
                )
                fn(ctx)

        return DispatchResult(matched, applied)


def _match_triggers(event: EventRecord, triggers: list[TriggerSpec]) -> bool:
    if not triggers:
        return False
    for trig in triggers:
        if trig.always:
            return True
        if trig.on_event:
            try:
                want = EventKind(trig.on_event)
            except ValueError as exc:
                raise ValueError(f"unknown event kind in trigger: {trig.on_event}") from exc
            if event.kind == want:
                return True
        if trig.on_action_kind:
            kind_in_payload = event.payload.get("action_kind")
            if kind_in_payload == trig.on_action_kind:
                return True
    return False


def _check_conditions(
    event: EventRecord,
    rule: MotivationRule,
    task_state,
    kernel,
) -> bool:
    for cond in rule.conditions:
        fn = CONDITIONS.get(cond.kind)
        if fn is None:
            raise ValueError(f"unknown condition kind: {cond.kind}")
        ctx = ConditionContext(
            event=event,
            task_state=task_state,
            kernel=kernel,
            rule_args=cond.args,
        )
        if not fn(ctx):
            return False
    return True
