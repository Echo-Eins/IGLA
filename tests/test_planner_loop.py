"""End-to-end tests of the planner loop using the OfflineCannedClient."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from conftest import build_runtime  # type: ignore[import-not-found]

from igla.ids import prefixed_id
from igla.kernel.clock import StepClock
from igla.motivation.effects import EFFECTS as _EFFECTS
from igla.motivation.effects import EffectContext as _EffCtx
from igla.protocol.event import EventKind
from igla.protocol.runtime import RuntimeMode
from igla.protocol.task import TaskSpec, TaskStatus
from igla.todo.tree import TodoTree


def _new_task(text: str, kernel) -> TaskSpec:
    return TaskSpec(
        task_id=prefixed_id("task"),
        raw_request=text,
        goal=text,
        status=TaskStatus.READY,
        created_at=kernel.clock.now(),
    )


def test_declare_task_done_immediately(make_settings) -> None:
    settings = make_settings()
    canned = [
        {
            "action": "declare_task_done",
            "summary": "nothing to do",
            "reason": "user just typed hello",
        }
    ]
    planner, kernel, store, _, _ = build_runtime(
        settings, canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("hello", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="hello")
    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.DONE
    assert outcome.summary == "nothing to do"


def test_branch_then_complete_then_done(make_settings) -> None:
    settings = make_settings()
    canned = [
        # 1. branch root into one subgoal
        {
            "action": "todo_branch",
            "parent_node_id": "__ROOT__",  # placeholder; planner will re-resolve
            "reason": "split into one explicit step",
            "children": [{"title": "step 1", "kind": "subgoal"}],
        },
        # 2. mark that subgoal done; we don't know its id, so use noop_observe
        # which doesn't need to reference the node, then declare done.
        {
            "action": "tool_invocation",
            "tool_name": "noop_observe",
            "tool_version": "1.0.0",
            "input": {"note": "did the thing"},
            "reason": "do step 1",
        },
        {
            "action": "declare_task_done",
            "summary": "done",
            "reason": "everything covered",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings, canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("do x", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    root = todo.create_root(title="do x")
    # rewrite the canned proposal to point to the actual root id
    canned[0]["parent_node_id"] = root.node_id

    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.DONE
    # Root has at least one child after the branch.
    assert len([n for n in todo.all_nodes() if n.parent_id == root.node_id]) == 1


def test_ask_user_clarification_pauses_loop(make_settings) -> None:
    """Clarification is allowed AFTER autonomous discovery has been tried.

    ``discovery_before_clarification`` rejects ask_user_clarification on
    turn 1. The model must first invoke a read-only tool; once any such
    tool has run, asking the user is permitted.
    """
    settings = make_settings()
    canned = [
        # Autonomous discovery first — this satisfies discovery_before_clarification.
        {
            "action": "tool_invocation",
            "tool_name": "find_files",
            "tool_version": "1.0.0",
            "input": {"query": "backup-target"},
            "reason": "look for the backup target",
        },
        # Now clarification is unlocked.
        {
            "action": "ask_user_clarification",
            "question": "where to put it?",
            "reason": "need a path",
        },
        # After user reply, just declare done.
        {
            "action": "declare_task_done",
            "summary": "ok",
            "reason": "got the path",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings, canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("backup", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="backup")

    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.NEEDS_USER_CLARIFICATION
    # Resume with user reply.
    outcome = planner.handle_user_input(task=task, todo=todo, text="/tmp")
    assert outcome.status is TaskStatus.DONE


def test_malformed_question_is_rejected_before_discovery(make_settings) -> None:
    """A bare question-shaped model output must not reach the user on turn 1."""
    settings = make_settings()
    canned = [
        {
            "reason": "ask instead of searching",
            "question": "where is AGENTS.md?",
        },
        {
            "action": "tool_invocation",
            "tool_name": "find_files",
            "tool_version": "1.0.0",
            "input": {"query": "AGENTS.md", "max_results": 20},
            "reason": "search locally first",
        },
        {
            "action": "declare_task_done",
            "summary": "searched",
            "reason": "regression complete",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("найди файл AGENTS.md", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="find")

    outcome = planner.run_task(task, todo)

    assert outcome.status is TaskStatus.DONE
    rejections = [
        e
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.POLICY_REJECTION
    ]
    assert [e.payload["reason_code"] for e in rejections] == ["MUST_DISCOVER_FIRST"]
    completed_tools = [
        e.payload["tool_name"]
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.TOOL_INVOCATION_COMPLETED
    ]
    assert completed_tools == ["find_files"]


def test_ask_user_tool_is_rejected_before_discovery(make_settings) -> None:
    """The tool path must not bypass the clarification policy."""
    settings = make_settings()
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "ask_user",
            "tool_version": "1.0.0",
            "input": {"question": "where is AGENTS.md?"},
            "reason": "ask instead of searching",
        },
        {
            "action": "tool_invocation",
            "tool_name": "find_files",
            "tool_version": "1.0.0",
            "input": {"query": "AGENTS.md", "max_results": 20},
            "reason": "search locally first",
        },
        {
            "action": "declare_task_done",
            "summary": "searched",
            "reason": "regression complete",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("найди файл AGENTS.md", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="find")

    outcome = planner.run_task(task, todo)

    assert outcome.status is TaskStatus.DONE
    started_tools = [
        e.payload["tool_name"]
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.TOOL_INVOCATION_STARTED
    ]
    assert started_tools == ["find_files"]


def test_discovery_output_is_visible_to_next_planner_turn(make_settings) -> None:
    settings = make_settings()
    (settings.paths.workspace / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "find_files",
            "tool_version": "1.0.0",
            "input": {"query": "AGENTS.md", "max_results": 20},
            "reason": "search locally first",
        },
        {
            "action": "declare_task_done",
            "summary": "found AGENTS.md",
            "reason": "result is visible",
        },
    ]
    planner, kernel, _, _, llm = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("найди файл AGENTS.md", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="find")

    outcome = planner.run_task(task, todo)

    assert outcome.status is TaskStatus.DONE
    second_turn = json.loads(llm.sent_messages[1][2].content)
    rendered = json.dumps(second_turn["new_events_since_last_turn"], ensure_ascii=False)
    assert "AGENTS.md" in rendered
    assert "matches" in rendered


def test_question_after_unambiguous_discovery_is_rejected(make_settings) -> None:
    settings = make_settings()
    (settings.paths.workspace / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "find_files",
            "tool_version": "1.0.0",
            "input": {"query": "AGENTS.md", "max_results": 20},
            "reason": "search locally first",
        },
        {
            "action": "ask_user_clarification",
            "question": "where is AGENTS.md?",
            "reason": "bad model behavior despite result",
        },
        {
            "action": "declare_task_done",
            "summary": "found AGENTS.md",
            "reason": "use discovery result",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("найди файл AGENTS.md", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="find")

    outcome = planner.run_task(task, todo)

    assert outcome.status is TaskStatus.DONE
    rejections = [
        e
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.POLICY_REJECTION
    ]
    assert [e.payload["reason_code"] for e in rejections] == ["DISCOVERY_RESULT_AVAILABLE"]


def test_unknown_tool_is_rejected_and_planner_retries(make_settings) -> None:
    settings = make_settings()
    # Two proposals: first is bogus tool, second is declare_task_done.
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "no_such_tool",
            "tool_version": "1.0.0",
            "input": {},
            "reason": "trying",
        },
        {
            "action": "declare_task_done",
            "summary": "abandoning",
            "reason": "tool missing",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings, canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("x", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="x")

    outcome = planner.run_task(task, todo)
    rejections = [
        e for e in kernel.events.list_by_task(task.task_id) if e.kind is EventKind.POLICY_REJECTION
    ]
    assert len(rejections) == 1
    assert outcome.status is TaskStatus.DONE


def test_failed_tool_enters_diagnosis_and_persists_through_clarification(
    make_settings,
) -> None:
    """End-to-end: failure → diagnosis → clarify → resume preserves diagnosis."""
    settings = make_settings()
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "read_file",
            "tool_version": "1.0.0",
            "input": {"path": "no_such_file.txt"},
            "reason": "look at the file",
        },
        # In diagnosis mode broad tool_invocation is blocked, but the
        # planner may still ask the user if discovery cannot proceed.
        {
            "action": "ask_user_clarification",
            "question": "the file is missing — what now?",
            "reason": "diagnose",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("inspect file", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="inspect")

    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.NEEDS_USER_CLARIFICATION

    failed = [
        e
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.TOOL_INVOCATION_FAILED
    ]
    assert failed

    # While paused, the saved pre-pause state must remember diagnosis.
    state = kernel.state.get_state(task.task_id)
    assert state.mode is RuntimeMode.NEEDS_USER_CLARIFICATION
    assert state.pre_pause_mode is RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED
    assert "tool:find_files" in state.pre_pause_allowed
    assert "tool:search_text" in state.pre_pause_allowed
    assert "declare_task_done" in state.pre_pause_forbidden

    # Simulate the resume effect explicitly (no further canned responses).
    evt = kernel.events.append(
        kind=EventKind.USER_INPUT_RECEIVED,
        actor="user",
        task_id=task.task_id,
        payload={"text": "abort"},
    )
    _EFFECTS["resume_from_clarification"](
        _EffCtx(event=evt, task_state=state, kernel=kernel, rule_id="test", rule_args={})
    )
    assert state.mode is RuntimeMode.FAILURE_DIAGNOSIS_REQUIRED
    assert "declare_task_done" in state.forbidden_next_actions
    assert state.pre_pause_mode is None


def test_open_file_flow_can_discover_then_read(make_settings) -> None:
    settings = make_settings()
    docs = settings.paths.workspace / "docs"
    docs.mkdir(parents=True)
    review = docs / "00-review.md"
    review.write_text("# Review\n", encoding="utf-8")
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "find_files",
            "tool_version": "1.0.0",
            "input": {"query": "00-review.md", "max_results": 5},
            "reason": "Find the file locally before asking the user for its path.",
        },
        {
            "action": "tool_invocation",
            "tool_name": "read_file",
            "tool_version": "1.0.0",
            "input": {"path": "docs/00-review.md"},
            "reason": "Read the discovered file.",
        },
        {
            "action": "declare_task_done",
            "summary": "file opened",
            "reason": "The requested file was found and read.",
        },
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings,
        canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("Открой файл 00-review.md", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="open")

    outcome = planner.run_task(task, todo)

    assert outcome.status is TaskStatus.DONE
    completed = [
        e
        for e in kernel.events.list_by_task(task.task_id)
        if e.kind is EventKind.TOOL_INVOCATION_COMPLETED
    ]
    assert [e.payload["tool_name"] for e in completed] == ["find_files", "read_file"]


def test_policy_rejection_loops_until_max(make_settings) -> None:
    settings = make_settings()
    # Repeatedly propose unknown tool — should hit max_consecutive_rejections.
    canned = [
        {
            "action": "tool_invocation",
            "tool_name": "no_such_tool",
            "tool_version": "1.0.0",
            "input": {},
            "reason": "trying",
        }
        for _ in range(10)
    ]
    planner, kernel, _, _, _ = build_runtime(
        settings, canned_responses=canned,
        clock=StepClock(datetime(2026, 4, 29, tzinfo=UTC), step_seconds=0.1),
    )
    task = _new_task("x", kernel)
    todo = TodoTree(task.task_id, kernel.clock)
    todo.create_root(title="x")

    outcome = planner.run_task(task, todo)
    assert outcome.status is TaskStatus.ABORTED
    assert outcome.aborted_reason == "MAX_CONSECUTIVE_REJECTIONS"
