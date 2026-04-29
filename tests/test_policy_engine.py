"""Policy engine + constitution predicates."""
from __future__ import annotations

from datetime import datetime, timezone

from igla.kernel.clock import StepClock
from igla.kernel.kernel import Kernel
from igla.policies.constitution import load_constitution
from igla.policies.engine import PolicyContext, PolicyEngine
from igla.protocol.policy import ActionRequest, PolicyDecisionKind
from igla.protocol.runtime import RuntimeMode
from igla.tools.builtin.noop_observe import NoopObserveTool


def _build(make_settings):
    settings = make_settings()
    kernel = Kernel(settings, clock=StepClock(start=datetime(2026, 4, 29, tzinfo=timezone.utc)))
    kernel.registry.register(NoopObserveTool())
    constitution = load_constitution(settings.paths.constitution_file)
    engine = PolicyEngine(constitution, PolicyContext())
    return engine, kernel


def test_unknown_tool_is_denied(make_settings) -> None:
    engine, kernel = _build(make_settings)
    kernel.state.ensure_task("t1")
    kernel.state.set_allowed_actions("t1", ["tool_invocation"])
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="not_real",
        tool_version="1.0.0",
        reason="x",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.is_deny
    assert decision.rejection.reason_code == "UNKNOWN_TOOL"


def test_input_schema_violation_is_denied(make_settings) -> None:
    engine, kernel = _build(make_settings)
    kernel.state.ensure_task("t1")
    kernel.state.set_allowed_actions("t1", ["tool_invocation"])
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="noop_observe",
        tool_version="1.0.0",
        input={},  # missing required "note"
        reason="x",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.is_deny
    assert decision.rejection.reason_code == "INVALID_INPUT_SCHEMA"


def test_action_not_in_allowed_list_denied(make_settings) -> None:
    engine, kernel = _build(make_settings)
    kernel.state.ensure_task("t1")
    kernel.state.set_allowed_actions("t1", ["ask_user_clarification"])
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="noop_observe",
        tool_version="1.0.0",
        input={"note": "x"},
        reason="x",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.is_deny
    assert decision.rejection.reason_code == "ACTION_NOT_IN_ALLOWED_LIST"


def test_no_blind_retry_blocks_tool_invocation_in_diagnosis(make_settings) -> None:
    engine, kernel = _build(make_settings)
    kernel.state.ensure_task("t1")
    kernel.state.transition_mode("t1", RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED)
    kernel.state.set_allowed_actions("t1", ["tool_invocation"])
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="noop_observe",
        tool_version="1.0.0",
        input={"note": "x"},
        reason="x",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.is_deny
    assert decision.rejection.reason_code == "NO_BLIND_RETRY"


def test_allow_when_clarification_in_diagnosis(make_settings) -> None:
    engine, kernel = _build(make_settings)
    kernel.state.ensure_task("t1")
    kernel.state.transition_mode("t1", RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED)
    kernel.state.set_allowed_actions("t1", ["ask_user_clarification"])
    action = ActionRequest(
        kind="ask_user_clarification",
        actor="planner",
        task_id="t1",
        input={"question": "what next?"},
        reason="x",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.decision is PolicyDecisionKind.ALLOW
