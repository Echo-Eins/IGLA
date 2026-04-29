"""Runtime state machine.

The state machine is intentionally explicit: every transition is registered
and any unknown transition raises ``StateTransitionError``. This makes it
hard to silently drift into illegal states.

Two automata coexist:

* **Step status** — per-PlanStep.
* **Runtime mode** — per-task.

Mode transitions are largely driven by the **motivation engine** (see
``motivation/``); this module just holds the data and validates moves.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ..protocol.plan import StepStatus
from ..protocol.runtime import RuntimeMode, RuntimeStateSnapshot
from .clock import Clock
from .errors import StateTransitionError

_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PROPOSED: frozenset({StepStatus.APPROVED, StepStatus.SKIPPED, StepStatus.BLOCKED}),
    StepStatus.APPROVED: frozenset({StepStatus.RUNNING, StepStatus.BLOCKED, StepStatus.SKIPPED}),
    StepStatus.RUNNING: frozenset(
        {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.BLOCKED}
    ),
    StepStatus.COMPLETED: frozenset({StepStatus.ROLLED_BACK}),
    StepStatus.FAILED: frozenset({StepStatus.PROPOSED, StepStatus.SKIPPED, StepStatus.BLOCKED}),
    StepStatus.BLOCKED: frozenset({StepStatus.PROPOSED, StepStatus.APPROVED, StepStatus.SKIPPED}),
    StepStatus.ROLLED_BACK: frozenset({StepStatus.PROPOSED, StepStatus.SKIPPED}),
    StepStatus.SKIPPED: frozenset(),
}


_MODE_TRANSITIONS: dict[RuntimeMode, frozenset[RuntimeMode]] = {
    RuntimeMode.READY: frozenset(
        {
            RuntimeMode.WAITING_FOR_TOOL_RESULT,
            RuntimeMode.NEEDS_USER_CLARIFICATION,
            RuntimeMode.NEEDS_USER_APPROVAL,
            RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED,
            RuntimeMode.BLOCKED,
            RuntimeMode.TASK_DONE,
        }
    ),
    RuntimeMode.WAITING_FOR_TOOL_RESULT: frozenset(
        {
            RuntimeMode.READY,
            RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED,
            RuntimeMode.NEEDS_USER_CLARIFICATION,
            RuntimeMode.BLOCKED,
            RuntimeMode.TASK_DONE,
        }
    ),
    RuntimeMode.NEEDS_USER_CLARIFICATION: frozenset(
        {
            RuntimeMode.READY,
            RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED,
            RuntimeMode.BLOCKED,
            RuntimeMode.TASK_DONE,
        }
    ),
    RuntimeMode.NEEDS_USER_APPROVAL: frozenset(
        {
            RuntimeMode.READY,
            RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED,
            RuntimeMode.BLOCKED,
            RuntimeMode.TASK_DONE,
        }
    ),
    RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED: frozenset(
        {
            RuntimeMode.READY,
            RuntimeMode.NEEDS_USER_CLARIFICATION,
            RuntimeMode.NEEDS_USER_APPROVAL,
            RuntimeMode.BLOCKED,
            RuntimeMode.TASK_DONE,
        }
    ),
    RuntimeMode.BLOCKED: frozenset(
        {RuntimeMode.READY, RuntimeMode.NEEDS_USER_CLARIFICATION, RuntimeMode.TASK_DONE}
    ),
    RuntimeMode.TASK_DONE: frozenset(),
}


@dataclass
class TaskRuntimeState:
    """Mutable per-task state owned by the kernel."""

    task_id: str
    mode: RuntimeMode = RuntimeMode.READY
    iteration: int = 0
    consecutive_rejections: int = 0

    last_event_id: str | None = None
    last_step_id: str | None = None
    last_error_code: str | None = None
    last_rejection_reason_code: str | None = None
    last_rejection_message: str | None = None

    allowed_next_actions: list[str] = field(default_factory=list)
    forbidden_next_actions: list[str] = field(default_factory=list)

    failure_classified: bool = False
    changed_condition_declared: bool = False

    # Snapshot of (mode, allowed, forbidden) saved when the task pauses for
    # user clarification, so we can restore the *prior* operational state on
    # resume — including FAILURE_DIAGNOSIS_REQUIRED, which must persist
    # across clarifications.
    pre_pause_mode: RuntimeMode | None = None
    pre_pause_allowed: list[str] = field(default_factory=list)
    pre_pause_forbidden: list[str] = field(default_factory=list)

    def snapshot(self, clock: Clock) -> RuntimeStateSnapshot:
        return RuntimeStateSnapshot(
            task_id=self.task_id,
            mode=self.mode,
            iteration=self.iteration,
            consecutive_rejections=self.consecutive_rejections,
            last_event_id=self.last_event_id,
            last_step_id=self.last_step_id,
            last_error_code=self.last_error_code,
            last_rejection_reason_code=self.last_rejection_reason_code,
            last_rejection_message=self.last_rejection_message,
            allowed_next_actions=list(self.allowed_next_actions),
            forbidden_next_actions=list(self.forbidden_next_actions),
            failure_classified=self.failure_classified,
            changed_condition_declared=self.changed_condition_declared,
            captured_at=clock.now(),
        )


class StateMachine:
    """Validates transitions and stores the per-task runtime state."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._tasks: dict[str, TaskRuntimeState] = {}

    # --- task state ----------------------------------------------------

    def ensure_task(self, task_id: str) -> TaskRuntimeState:
        state = self._tasks.get(task_id)
        if state is None:
            state = TaskRuntimeState(task_id=task_id)
            self._tasks[task_id] = state
        return state

    def get_state(self, task_id: str) -> TaskRuntimeState:
        if task_id not in self._tasks:
            raise StateTransitionError(f"unknown task_id: {task_id}")
        return self._tasks[task_id]

    def transition_mode(self, task_id: str, target: RuntimeMode) -> None:
        state = self.ensure_task(task_id)
        if target == state.mode:
            return
        allowed = _MODE_TRANSITIONS.get(state.mode, frozenset())
        if target not in allowed:
            raise StateTransitionError(
                f"illegal mode transition for {task_id}: {state.mode.value} -> {target.value}"
            )
        state.mode = target

    # --- step status ---------------------------------------------------

    def transition_step(self, current: StepStatus, target: StepStatus) -> StepStatus:
        if current == target:
            return target
        allowed = _STEP_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise StateTransitionError(
                f"illegal step transition: {current.value} -> {target.value}"
            )
        return target

    # --- helpers -------------------------------------------------------

    def set_allowed_actions(
        self, task_id: str, allowed: Iterable[str], forbidden: Iterable[str] = ()
    ) -> None:
        state = self.ensure_task(task_id)
        state.allowed_next_actions = list(allowed)
        state.forbidden_next_actions = list(forbidden)

    def increment_iteration(self, task_id: str) -> int:
        state = self.ensure_task(task_id)
        state.iteration += 1
        return state.iteration

    def record_rejection(self, task_id: str, *, reason_code: str, message: str) -> int:
        state = self.ensure_task(task_id)
        state.consecutive_rejections += 1
        state.last_rejection_reason_code = reason_code
        state.last_rejection_message = message
        return state.consecutive_rejections

    def reset_rejections(self, task_id: str) -> None:
        state = self.ensure_task(task_id)
        state.consecutive_rejections = 0
        state.last_rejection_reason_code = None
        state.last_rejection_message = None

    def mark_failure(self, task_id: str, error_code: str | None) -> None:
        state = self.ensure_task(task_id)
        state.last_error_code = error_code
        state.failure_classified = False
        state.changed_condition_declared = False

    def mark_failure_classified(self, task_id: str) -> None:
        state = self.ensure_task(task_id)
        state.failure_classified = True

    def mark_changed_condition(self, task_id: str) -> None:
        state = self.ensure_task(task_id)
        state.changed_condition_declared = True

    def now(self) -> datetime:
        return self._clock.now()
