"""Motivation engine.

This is the easily-reworkable layer that the user touches when they want
to change *behaviour*: which actions are available in which mode, when the
runtime should enter or exit ``FAILURE_DIAGNOSIS_REQUIRED``, etc.

Concepts:

* **Trigger** — picks rules to evaluate based on an incoming event.
* **Condition** — boolean check on event + state + context.
* **Effect** — mutates ``TaskRuntimeState`` (mode, allowed/forbidden actions,
  flags). Effects are the only place where motivation rules can change the
  world.
* **Rule** — ``triggers + conditions + effects + meta``.

Rules are loaded from ``configs/motivation.yaml``; conditions and effects
are pluggable: extend by registering new functions in
``conditions.py`` / ``effects.py``.

Cycle: after every event, ``MotivationCycle.dispatch(event, state, kernel)``
is called. The cycle picks all rules whose triggers match, runs their
conditions, and applies effects of those that pass. Rules are independent
and cumulative — they cannot "veto" each other; the deterministic ordering
is by ``rule.priority`` ascending then by file order.
"""
from .conditions import (
    CONDITIONS,
    ConditionContext,
    register_condition,
)
from .cycle import MotivationCycle
from .effects import EFFECTS, EffectContext, register_effect
from .rule import (
    EffectSpec,
    MotivationRule,
    RuleConditionSpec,
    TriggerSpec,
    load_rules,
)

__all__ = [
    "CONDITIONS",
    "ConditionContext",
    "EFFECTS",
    "EffectContext",
    "EffectSpec",
    "MotivationCycle",
    "MotivationRule",
    "RuleConditionSpec",
    "TriggerSpec",
    "load_rules",
    "register_condition",
    "register_effect",
]
