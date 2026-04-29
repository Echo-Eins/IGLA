"""Policy engine and constitution.

The policy layer is *the* gate between the planner's proposals and the
executor. There are two kinds of policy code:

* **Constitution** — hard, code-level invariants (``predicates.py``).
  They cannot be disabled via config; only flagged off in extreme tests.
* **Action Gates** — declarative gates loaded from YAML
  (``configs/constitution.yaml``).

The motivation engine (``igla.motivation``) is a complementary, event-driven
system that updates runtime mode and allowed/forbidden actions. The policy
engine then refers to that mutable state when deciding individual actions.
"""
from .constitution import Constitution, ConstitutionEntry, load_constitution
from .engine import PolicyContext, PolicyEngine
from .predicates import (
    PREDICATES,
    PredicateContext,
    PredicateOutcome,
    register_predicate,
)

__all__ = [
    "Constitution",
    "ConstitutionEntry",
    "PolicyContext",
    "PolicyEngine",
    "PredicateContext",
    "PredicateOutcome",
    "PREDICATES",
    "load_constitution",
    "register_predicate",
]
