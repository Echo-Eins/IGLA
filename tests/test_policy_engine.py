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
from igla.tools.builtin.search import FindFilesTool


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


def test_no_blind_retry_allows_explicit_read_only_diagnostic_tool(make_settings) -> None:
    engine, kernel = _build(make_settings)
    kernel.registry.register(FindFilesTool(workspace_root=str(kernel.workspace)))
    kernel.state.ensure_task("t1")
    kernel.state.transition_mode("t1", RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED)
    kernel.state.set_allowed_actions("t1", ["tool:find_files"])
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="find_files",
        tool_version="1.0.0",
        input={"query": "00-review.md"},
        reason="locate missing file",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.decision is PolicyDecisionKind.ALLOW


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


def test_clarification_blocked_before_any_discovery(make_settings) -> None:
    """First-turn ask_user_clarification must be denied with MUST_DISCOVER_FIRST."""
    engine, kernel = _build(make_settings)
    kernel.registry.register(FindFilesTool(workspace_root="/tmp"))
    kernel.state.ensure_task("t1")
    # Default READY mode + clarification in allowed list (as bootstrap motivation does).
    kernel.state.set_allowed_actions("t1", ["ask_user_clarification", "tool_invocation"])
    action = ActionRequest(
        kind="ask_user_clarification",
        actor="planner",
        task_id="t1",
        input={"question": "what file did you mean?"},
        reason="no clue",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.is_deny
    assert decision.rejection.reason_code == "MUST_DISCOVER_FIRST"


def test_clarification_allowed_after_readonly_tool(make_settings) -> None:
    """A completed read-only tool invocation unlocks ask_user_clarification."""
    from igla.protocol.event import EventKind

    engine, kernel = _build(make_settings)
    kernel.registry.register(FindFilesTool(workspace_root="/tmp"))
    kernel.state.ensure_task("t1")
    kernel.state.set_allowed_actions("t1", ["ask_user_clarification", "tool_invocation"])
    # Simulate that the planner already ran find_files.
    kernel.events.append(
        kind=EventKind.TOOL_INVOCATION_COMPLETED,
        actor="tool:find_files",
        task_id="t1",
        payload={"tool_name": "find_files", "status": "success"},
    )
    action = ActionRequest(
        kind="ask_user_clarification",
        actor="planner",
        task_id="t1",
        input={"question": "didn't find it — clarify?"},
        reason="empty result",
    )
    decision = engine.check(action, kernel=kernel)
    assert decision.decision is PolicyDecisionKind.ALLOW
