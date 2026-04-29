"""Policy engine.

Iterates the active constitution against an ``ActionRequest`` and returns a
single ``PolicyDecision``. The first ``DENY`` wins. ``allowed_next_actions``
on the result is the union of the runtime state's current allowed list and
any hints attached by the deny outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..protocol.policy import (
    ActionRequest,
    PolicyDecision,
    PolicyDecisionKind,
)
from .constitution import Constitution
from .predicates import PredicateContext


@dataclass
class PolicyContext:
    """Runtime parameters passed to predicates."""

    iteration_limit: int = 60
    clarification_depth_limit: int = 4
    consecutive_rejections_limit: int = 5


class PolicyEngine:
    def __init__(self, constitution: Constitution, context: PolicyContext) -> None:
        self._constitution = constitution
        self._context = context

    @property
    def constitution(self) -> Constitution:
        return self._constitution

    def check(
        self,
        action: ActionRequest,
        *,
        kernel,  # noqa: ANN001 — typed in caller
        todo_tree=None,
    ) -> PolicyDecision:
        task_state = kernel.state.ensure_task(action.task_id)
        ctx = PredicateContext(
            action=action,
            task_state=task_state,
            kernel=kernel,
            todo_tree=todo_tree,
            iteration_limit=self._context.iteration_limit,
            clarification_depth_limit=self._context.clarification_depth_limit,
            consecutive_rejections_limit=self._context.consecutive_rejections_limit,
        )

        for entry in self._constitution.enabled():
            outcome = entry.predicate(ctx)
            if outcome.decision is PolicyDecisionKind.ALLOW:
                continue
            allowed_next = list(outcome.allowed_next) or list(task_state.allowed_next_actions)
            forbidden_next = list(outcome.forbidden_next) or list(
                task_state.forbidden_next_actions
            )
            return PolicyDecision(
                decision=outcome.decision,
                action=action,
                rejection=outcome.rejection,
                allowed_next_actions=allowed_next,
                forbidden_next_actions=forbidden_next,
                rationale=f"rule:{entry.rule_id}",
            )

        return PolicyDecision(
            decision=PolicyDecisionKind.ALLOW,
            action=action,
            allowed_next_actions=list(task_state.allowed_next_actions),
            forbidden_next_actions=list(task_state.forbidden_next_actions),
        )
