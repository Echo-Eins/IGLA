"""Constitution predicates.

A predicate is a small pure function that checks one invariant on an
``ActionRequest``. Predicates are registered via ``register_predicate`` and
referenced by name in ``configs/constitution.yaml``.

To add a new invariant later:

1. Write a function ``def my_check(ctx) -> PredicateOutcome``.
2. ``register_predicate("my_check", my_check)``.
3. Add it to ``constitution.yaml``.

The signature is intentionally narrow: predicates do not access the full
kernel; they get a ``PredicateContext`` carrying just what they need.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..protocol.event import EventKind
from ..protocol.policy import ActionRequest, PolicyDecisionKind, PolicyRejection
from ..protocol.runtime import RuntimeMode

if TYPE_CHECKING:  # pragma: no cover
    from ..kernel.kernel import Kernel
    from ..kernel.state_machine import TaskRuntimeState
    from ..todo.tree import TodoTree


@dataclass
class PredicateContext:
    """Everything a predicate needs to decide.

    Predicates may *read* but never *mutate* state through this context.
    """

    action: ActionRequest
    task_state: "TaskRuntimeState"
    kernel: "Kernel"
    todo_tree: "TodoTree | None" = None
    iteration_limit: int = 60
    clarification_depth_limit: int = 4
    consecutive_rejections_limit: int = 5


@dataclass(frozen=True)
class PredicateOutcome:
    decision: PolicyDecisionKind
    rejection: PolicyRejection | None = None
    allowed_next: tuple[str, ...] = ()
    forbidden_next: tuple[str, ...] = ()

    @classmethod
    def allow(cls) -> "PredicateOutcome":
        return cls(decision=PolicyDecisionKind.ALLOW)

    @classmethod
    def deny(
        cls,
        *,
        reason_code: str,
        message: str,
        rule_id: str | None = None,
        missing: Iterable[str] = (),
        hints: Iterable[str] = (),
        allowed_next: Iterable[str] = (),
        forbidden_next: Iterable[str] = (),
    ) -> "PredicateOutcome":
        return cls(
            decision=PolicyDecisionKind.DENY,
            rejection=PolicyRejection(
                reason_code=reason_code,
                message=message,
                rule_id=rule_id,
                missing_requirements=list(missing),
                hints=list(hints),
            ),
            allowed_next=tuple(allowed_next),
            forbidden_next=tuple(forbidden_next),
        )


PredicateFn = Callable[[PredicateContext], PredicateOutcome]


PREDICATES: dict[str, PredicateFn] = {}


def register_predicate(name: str, fn: PredicateFn) -> None:
    if name in PREDICATES:
        raise ValueError(f"predicate already registered: {name}")
    PREDICATES[name] = fn


# ---------------------------------------------------------------------------
#  Built-in predicates
# ---------------------------------------------------------------------------


def _registry_known_tool(ctx: PredicateContext) -> PredicateOutcome:
    """Tool-invocation actions must reference a registered tool."""
    if ctx.action.kind != "tool_invocation":
        return PredicateOutcome.allow()
    name = ctx.action.tool_name
    if not name:
        return PredicateOutcome.deny(
            reason_code="MISSING_TOOL_NAME",
            message="tool_invocation requires tool_name",
            rule_id="registry_known_tool",
        )
    if not ctx.kernel.registry.has(name, ctx.action.tool_version):
        available = ", ".join(m.name for m in ctx.kernel.registry.list_tools())
        return PredicateOutcome.deny(
            reason_code="UNKNOWN_TOOL",
            message=f"tool not found in registry: {name}@{ctx.action.tool_version or '*'}",
            rule_id="registry_known_tool",
            hints=[f"available tools: {available}"],
        )
    return PredicateOutcome.allow()


def _schema_validated(ctx: PredicateContext) -> PredicateOutcome:
    """Tool input must validate against the manifest's input schema.

    We perform a defensive pre-check here so the planner gets a structured
    rejection (with allowed_next pointing at clarification) instead of a
    generic ``ValidationError`` from the executor.
    """
    if ctx.action.kind != "tool_invocation" or ctx.action.tool_name is None:
        return PredicateOutcome.allow()
    try:
        manifest = ctx.kernel.registry.get(ctx.action.tool_name, ctx.action.tool_version)
    except Exception:
        return PredicateOutcome.allow()  # _registry_known_tool reports it
    try:
        ctx.kernel.validator.validate_input(manifest, ctx.action.input)
    except Exception as exc:  # noqa: BLE001
        return PredicateOutcome.deny(
            reason_code="INVALID_INPUT_SCHEMA",
            message=f"input does not match {manifest.name} schema: {exc}",
            rule_id="schema_validated",
            allowed_next=("ask_user_clarification", "todo_branch"),
        )
    return PredicateOutcome.allow()


def _max_iterations(ctx: PredicateContext) -> PredicateOutcome:
    """Hard cap on iterations per task."""
    if ctx.task_state.iteration < ctx.iteration_limit:
        return PredicateOutcome.allow()
    return PredicateOutcome.deny(
        reason_code="MAX_ITERATIONS_EXCEEDED",
        message=f"task exceeded {ctx.iteration_limit} iterations",
        rule_id="max_iterations",
        allowed_next=("declare_task_done",),
    )


def _max_consecutive_rejections(ctx: PredicateContext) -> PredicateOutcome:
    if ctx.task_state.consecutive_rejections < ctx.consecutive_rejections_limit:
        return PredicateOutcome.allow()
    return PredicateOutcome.deny(
        reason_code="TOO_MANY_REJECTIONS",
        message=(
            f"planner produced {ctx.task_state.consecutive_rejections} consecutive "
            f"rejected proposals; aborting to avoid loops"
        ),
        rule_id="max_consecutive_rejections",
        allowed_next=("declare_task_done",),
    )


def _allowed_actions_respected(ctx: PredicateContext) -> PredicateOutcome:
    """The planner must only choose actions in ``allowed_next_actions``.

    Rules:
    * If the runtime has set an ``allowed_next_actions`` list, the proposal's
      *kind* (or its tool name for tool invocations) must appear there.
    * If ``forbidden_next_actions`` is set, the proposal must not match any
      entry.

    The format of allowed/forbidden entries:
      * ``"<action_kind>"`` — matches any action of that kind.
      * ``"tool:<name>"`` — matches a tool invocation by tool name.
    """

    allowed = ctx.task_state.allowed_next_actions
    forbidden = ctx.task_state.forbidden_next_actions

    candidate_keys = _action_keys(ctx.action)

    if forbidden and any(k in forbidden for k in candidate_keys):
        return PredicateOutcome.deny(
            reason_code="ACTION_FORBIDDEN_IN_CURRENT_MODE",
            message=(
                f"action {candidate_keys[0]} is forbidden in mode {ctx.task_state.mode.value}"
            ),
            rule_id="allowed_actions_respected",
            allowed_next=tuple(allowed),
            forbidden_next=tuple(forbidden),
        )

    if allowed and not any(k in allowed for k in candidate_keys):
        return PredicateOutcome.deny(
            reason_code="ACTION_NOT_IN_ALLOWED_LIST",
            message=(
                f"action {candidate_keys[0]} is not allowed; "
                f"allowed: {sorted(allowed)}"
            ),
            rule_id="allowed_actions_respected",
            allowed_next=tuple(allowed),
            forbidden_next=tuple(forbidden),
        )
    return PredicateOutcome.allow()


def _action_keys(action: ActionRequest) -> tuple[str, ...]:
    if action.kind == "tool_invocation" and action.tool_name:
        return (f"tool:{action.tool_name}", action.kind)
    return (action.kind,)


def _no_blind_retry(ctx: PredicateContext) -> PredicateOutcome:
    """In FAILURE_DIAGNOSIS_REQUIRED mode the planner must change the world
    only after declaring it has classified the failure or that conditions
    changed.

    The set of "diagnostic" actions that are always allowed is governed by
    motivation rules (see ``motivation/rules.yaml``). This predicate only
    enforces that mutating actions cannot escape the diagnosis mode without
    the corresponding state flags."""
    state = ctx.task_state
    if state.mode is not RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED:
        return PredicateOutcome.allow()
    # only allow "diagnostic" or innocuous actions
    diagnostic = {
        "ask_user_clarification",
        "todo_branch",
        "todo_complete",
        "declare_task_done",
    }
    if ctx.action.kind in diagnostic:
        return PredicateOutcome.allow()
    if _is_allowed_read_only_tool(ctx):
        return PredicateOutcome.allow()
    if state.failure_classified and state.changed_condition_declared:
        return PredicateOutcome.allow()
    return PredicateOutcome.deny(
        reason_code="NO_BLIND_RETRY",
        message=(
            "Previous step failed. Diagnose first: ask_user_clarification, "
            "todo_branch, todo_complete, or an allowed read-only diagnostic tool."
        ),
        rule_id="no_blind_retry",
        allowed_next=tuple(sorted({*diagnostic, *state.allowed_next_actions})),
    )


def _is_allowed_read_only_tool(ctx: PredicateContext) -> bool:
    if ctx.action.kind != "tool_invocation" or not ctx.action.tool_name:
        return False
    if f"tool:{ctx.action.tool_name}" not in ctx.task_state.allowed_next_actions:
        return False
    try:
        manifest = ctx.kernel.registry.get(ctx.action.tool_name, ctx.action.tool_version)
    except Exception:
        return False
    return manifest.risk_level == "read_only" and not manifest.side_effects


def _clarification_depth(ctx: PredicateContext) -> PredicateOutcome:
    """Limit how deeply the planner can spawn clarification subtrees."""
    if ctx.action.kind not in {"todo_branch", "ask_user_clarification"}:
        return PredicateOutcome.allow()
    if ctx.todo_tree is None:
        return PredicateOutcome.allow()
    depth = ctx.todo_tree.max_depth() + 1
    if depth <= ctx.clarification_depth_limit:
        return PredicateOutcome.allow()
    return PredicateOutcome.deny(
        reason_code="CLARIFICATION_DEPTH_EXCEEDED",
        message=(
            f"todo tree would reach depth {depth}; limit is "
            f"{ctx.clarification_depth_limit}"
        ),
        rule_id="clarification_depth",
        allowed_next=("declare_task_done", "todo_complete"),
    )


def _discovery_before_clarification(ctx: PredicateContext) -> PredicateOutcome:
    """IGLA must act, not ask.

    Reject ``ask_user_clarification`` proposals unless one of these is true:

    * the runtime is already in ``NEEDS_USER_CLARIFICATION`` (we are in an
      active back-and-forth — continuation is fine);
    * the runtime is in ``FAILURE_DIAGNOSIS_REQUIRED`` (recoverable failure
      where asking the user is a legitimate fallback);
    * at least one *read-only* discovery tool has already been invoked for
      this task (find_files / search_text / read_file / any tool whose
      manifest declares ``risk_level=read_only`` and ``side_effects=False``).

    The model is told via the rejection message exactly what to do next, so
    the next turn's prompt carries concrete, actionable hints rather than a
    naked ``ACTION_NOT_IN_ALLOWED_LIST``.
    """
    if ctx.action.kind != "ask_user_clarification":
        return PredicateOutcome.allow()

    state = ctx.task_state
    if state.mode in (
        RuntimeMode.NEEDS_USER_CLARIFICATION,
        RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED,
    ):
        return PredicateOutcome.allow()

    if _has_completed_readonly_tool(ctx):
        return PredicateOutcome.allow()

    discovery_hints = _discovery_tool_hints(ctx)
    return PredicateOutcome.deny(
        reason_code="MUST_DISCOVER_FIRST",
        message=(
            "IGLA не задаёт пользователю вопросов, пока сама не попробовала "
            "найти ответ. Сначала вызови один из read-only discovery tools "
            "(например find_files, search_text, read_file). "
            "ask_user_clarification разрешён только после неудачной попытки "
            "поиска или в режиме FAILURE_DIAGNOSIS_REQUIRED."
        ),
        rule_id="discovery_before_clarification",
        hints=discovery_hints,
        allowed_next=("tool_invocation", "todo_branch", "todo_complete"),
    )


def _has_completed_readonly_tool(ctx: PredicateContext) -> bool:
    """True if any read-only tool invocation finished for this task."""
    registry = ctx.kernel.registry
    for evt in ctx.kernel.events.list_by_task(ctx.action.task_id):
        if evt.kind not in (
            EventKind.TOOL_INVOCATION_COMPLETED,
            EventKind.TOOL_INVOCATION_FAILED,
        ):
            continue
        tool_name = evt.payload.get("tool_name")
        if not tool_name or tool_name == "ask_user":
            continue
        try:
            manifest = registry.get(tool_name)
        except Exception:
            # Tool gone from registry — still counts as a discovery attempt.
            return True
        if manifest.risk_level == "read_only" and not manifest.side_effects:
            return True
    return False


def _discovery_tool_hints(ctx: PredicateContext) -> tuple[str, ...]:
    names: list[str] = []
    for manifest in ctx.kernel.registry.list_tools():
        if manifest.name == "ask_user":
            continue
        if manifest.risk_level == "read_only" and not manifest.side_effects:
            names.append(manifest.name)
    if not names:
        return ()
    return (
        "Доступные read-only tools для автономного поиска: " + ", ".join(sorted(names)),
    )


def _todo_node_exists(ctx: PredicateContext) -> PredicateOutcome:
    """Actions that reference a TODO node must reference a node that exists."""
    if ctx.todo_tree is None:
        return PredicateOutcome.allow()
    target = ctx.action.todo_node_id
    if not target:
        return PredicateOutcome.allow()
    if not ctx.todo_tree.has(target):
        return PredicateOutcome.deny(
            reason_code="TODO_NODE_NOT_FOUND",
            message=f"TODO node not found: {target}",
            rule_id="todo_node_exists",
        )
    return PredicateOutcome.allow()


# ---------------------------------------------------------------------------


for _name, _fn in [
    ("registry_known_tool", _registry_known_tool),
    ("schema_validated", _schema_validated),
    ("max_iterations", _max_iterations),
    ("max_consecutive_rejections", _max_consecutive_rejections),
    ("allowed_actions_respected", _allowed_actions_respected),
    ("no_blind_retry", _no_blind_retry),
    ("clarification_depth", _clarification_depth),
    ("discovery_before_clarification", _discovery_before_clarification),
    ("todo_node_exists", _todo_node_exists),
]:
    register_predicate(_name, _fn)
