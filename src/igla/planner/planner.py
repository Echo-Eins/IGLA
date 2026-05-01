"""Planner loop.

This is the orchestration heart of the MVP. The pseudocode is:

```
while runtime.mode != TASK_DONE and iteration < max_iterations:
    iteration += 1
    proposal = LLM.complete(context_pack)
    parsed = PlannerProposal.parse(proposal)              # 1. type-check
    action = ActionRequest.from_proposal(parsed)
    decision = policy.check(action)                       # 2. policy-check
    if decision.is_deny:
        record rejection; continue                         # planner re-loops
    apply_action(parsed)                                  # 3. mutate
    motivation.dispatch(event)                            # 4. cycle update
    persist todo / event store
```

Each step is broken out into a small method so it can be tested in
isolation. ``Planner.run_task`` returns a ``PlannerOutcome`` that the chat
REPL renders to the user.
"""
from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from ..config import IglaSettings
from ..ids import prefixed_id
from ..kernel.kernel import Kernel
from ..motivation.cycle import MotivationCycle
from ..motivation.effects import EFFECTS as _EFFECTS
from ..motivation.effects import EffectContext as _EffCtx
from ..policies.engine import PolicyEngine
from ..protocol.event import EventKind
from ..protocol.invocation import (
    InvocationProvenance,
    ToolInvocation,
    ToolRef,
)
from ..protocol.policy import (
    ActionRequest,
    PolicyDecision,
    PolicyDecisionKind,
    PolicyRejection,
)
from ..protocol.proposal import (
    AskUserClarificationProposal,
    DeclareTaskDoneProposal,
    PlannerProposal,
    TodoBranchProposal,
    TodoCompleteProposal,
    ToolInvocationProposal,
)
from ..protocol.runtime import RuntimeMode
from ..protocol.task import TaskSpec, TaskStatus
from ..protocol.todo import TodoNodeKind, TodoStatus
from ..todo.store import TodoStore
from ..todo.tree import TodoTree
from .llm_client import LLMChatMessage, LLMClient
from .prompts import (
    build_proposal_messages,
    build_proposal_schema,
    build_session_system_message,
    build_task_system_message,
    render_event_tail,
    tools_digest_from_registry_dump,
)


@dataclass
class PlannerOutcome:
    task_id: str
    status: TaskStatus
    iterations: int
    last_decision: PolicyDecision | None
    summary: str | None
    aborted_reason: str | None = None


@dataclass(frozen=True)
class _TaskDoneSignal:
    summary: str | None = None


class PlannerError(Exception):
    pass


class Planner:
    def __init__(
        self,
        *,
        kernel: Kernel,
        settings: IglaSettings,
        policy: PolicyEngine,
        motivation: MotivationCycle,
        todo_store: TodoStore,
        llm: LLMClient,
    ) -> None:
        self._kernel = kernel
        self._settings = settings
        self._policy = policy
        self._motivation = motivation
        self._todo_store = todo_store
        self._llm = llm
        self._proposal_schema = build_proposal_schema()
        # KV-cache friendly prompt assembly:
        # * session system message — stable across all tasks; rebuilt only
        #   when the registered tools list changes.
        # * task system message — stable across all turns of one task.
        # * event index — last EventStore index already sent to the LLM;
        #   subsequent turns send only the delta.
        self._cached_session_msg: LLMChatMessage | None = None
        self._cached_session_tools_signature: tuple[tuple[str, str], ...] | None = None
        self._cached_task_msgs: dict[str, LLMChatMessage] = {}
        self._last_event_index_sent: dict[str, int] = {}

    # --- public API -----------------------------------------------------

    def run_task(self, task: TaskSpec, todo: TodoTree) -> PlannerOutcome:
        kernel = self._kernel
        kernel.state.ensure_task(task.task_id)
        kernel.receipts.mark_intake(task.task_id)
        # Emit TASK_CREATED exactly once per task. On resume (e.g. from
        # ``handle_user_input``) we skip it; otherwise the bootstrap
        # motivation rule would overwrite a paused/diagnosis state.
        already_created = any(
            evt.kind is EventKind.TASK_CREATED
            for evt in kernel.events.list_by_task(task.task_id)
        )
        if not already_created:
            evt = kernel.events.append(
                kind=EventKind.TASK_CREATED,
                actor="runtime",
                task_id=task.task_id,
                payload={"goal": task.goal, "raw_request": task.raw_request},
            )
            self._motivation.dispatch(evt)

        last_decision: PolicyDecision | None = None
        max_iterations = min(
            task.constraints.max_iterations,
            self._settings.planner.max_iterations_per_task,
        )

        while True:
            state = kernel.state.get_state(task.task_id)
            if state.mode is RuntimeMode.TASK_DONE:
                return PlannerOutcome(
                    task_id=task.task_id,
                    status=TaskStatus.DONE,
                    iterations=state.iteration,
                    last_decision=last_decision,
                    summary=state.last_rejection_message,
                )
            if state.mode is RuntimeMode.NEEDS_USER_CLARIFICATION:
                # Caller (chat REPL) is responsible for collecting user input
                # and re-driving the planner via ``handle_user_input``.
                return PlannerOutcome(
                    task_id=task.task_id,
                    status=TaskStatus.NEEDS_USER_CLARIFICATION,
                    iterations=state.iteration,
                    last_decision=last_decision,
                    summary=None,
                )
            if state.iteration >= max_iterations:
                return self._abort(task, "MAX_ITERATIONS_EXCEEDED")

            kernel.state.increment_iteration(task.task_id)
            try:
                proposal = self._ask_planner(
                    task=task, todo=todo, last_decision=last_decision
                )
            except _MalformedProposal as parse_exc:
                # Convert to a rejection so the next iteration tells the
                # model exactly what was wrong, and bump the rejection
                # counter so an endlessly-broken model still aborts.
                synthetic = self._malformed_to_decision(parse_exc, task)
                last_decision = synthetic
                rejection = synthetic.rejection
                if rejection is None:
                    raise PlannerError(
                        "malformed proposal decision has no rejection"
                    ) from parse_exc
                rejections = kernel.state.record_rejection(
                    task.task_id,
                    reason_code=rejection.reason_code,
                    message=rejection.message,
                )
                kernel.events.append(
                    kind=EventKind.POLICY_REJECTION,
                    actor="planner",
                    task_id=task.task_id,
                    payload={
                        "reason_code": "MALFORMED_PROPOSAL",
                        "message": str(parse_exc)[:200],
                        "raw": parse_exc.raw,
                    },
                )
                if rejections >= self._settings.planner.max_consecutive_rejections:
                    return self._abort(task, "MAX_CONSECUTIVE_REJECTIONS")
                continue
            except PlannerError:
                # LLM client/transport error — abort cleanly.
                return self._abort(task, "LLM_ERROR")

            decision = self._evaluate_proposal(proposal, todo=todo, task=task)
            last_decision = decision

            if decision.is_deny:
                rejection = decision.rejection
                rejections = kernel.state.record_rejection(
                    task.task_id,
                    reason_code=rejection.reason_code if rejection else "DENIED",
                    message=rejection.message if rejection else "policy deny",
                )
                kernel.events.append(
                    kind=EventKind.POLICY_REJECTION,
                    actor="policy",
                    task_id=task.task_id,
                    payload={
                        "reason_code": rejection.reason_code if rejection else None,
                        "message": rejection.message if rejection else None,
                        "rule_id": rejection.rule_id if rejection else None,
                        "action_kind": decision.action.kind,
                        "tool_name": decision.action.tool_name,
                    },
                )
                if rejections >= self._settings.planner.max_consecutive_rejections:
                    return self._abort(task, "MAX_CONSECUTIVE_REJECTIONS")
                continue

            kernel.state.reset_rejections(task.task_id)
            outcome = self._apply_action(proposal, task=task, todo=todo)
            if isinstance(outcome, _TaskDoneSignal):
                return PlannerOutcome(
                    task_id=task.task_id,
                    status=TaskStatus.DONE,
                    iterations=kernel.state.get_state(task.task_id).iteration,
                    last_decision=decision,
                    summary=outcome.summary or _summary_of(proposal),
                )

    def handle_user_input(
        self,
        *,
        task: TaskSpec,
        todo: TodoTree,
        text: str,
    ) -> PlannerOutcome:
        """Inject a user reply (after a NEEDS_USER_CLARIFICATION pause)."""
        evt = self._kernel.events.append(
            kind=EventKind.USER_INPUT_RECEIVED,
            actor="user",
            task_id=task.task_id,
            payload={"text": text},
        )
        self._motivation.dispatch(evt)
        # Find the most recent CLARIFYING node and record the answer there.
        clarifying = [
            n for n in todo.all_nodes() if n.status is TodoStatus.CLARIFYING
        ]
        if clarifying:
            clarifying.sort(key=lambda n: n.updated_at, reverse=True)
            todo.record_clarification_answer(clarifying[0].node_id, text)
            self._todo_store.save(todo)
        return self.run_task(task, todo)

    # --- planner internals --------------------------------------------

    def _ask_planner(
        self,
        *,
        task: TaskSpec,
        todo: TodoTree,
        last_decision: PolicyDecision | None,
    ) -> PlannerProposal:
        kernel = self._kernel
        snapshot = todo.snapshot()
        runtime_snapshot = kernel.state.get_state(task.task_id).snapshot(kernel.clock)
        events = kernel.events.list_by_task(task.task_id)

        # Event delta: only events the model has not seen yet.
        since = self._last_event_index_sent.get(task.task_id, 0)
        new_events = render_event_tail(events, since_index=since)

        # Session-level system message: rebuilt only when the registry changes.
        tools_dump: list[dict[str, Any]] = [
            {
                "name": m.name,
                "version": m.version,
                "description": m.description,
                "input_schema": m.input_schema,
                "risk_level": m.risk_level,
                "side_effects": m.side_effects,
            }
            for m in kernel.registry.list_tools()
        ]
        signature: tuple[tuple[str, str], ...] = tuple(
            sorted((str(t["name"]), str(t["version"])) for t in tools_dump)
        )
        if (
            self._cached_session_msg is None
            or self._cached_session_tools_signature != signature
        ):
            self._cached_session_msg = build_session_system_message(
                tools_digest_from_registry_dump(tools_dump)
            )
            self._cached_session_tools_signature = signature

        # Task-level system message: cached per task_id.
        task_msg = self._cached_task_msgs.get(task.task_id)
        if task_msg is None:
            task_msg = build_task_system_message(task)
            self._cached_task_msgs[task.task_id] = task_msg

        messages = build_proposal_messages(
            task=task,
            runtime=runtime_snapshot,
            todo=snapshot,
            last_decision=last_decision,
            new_events=new_events,
            available_tools=tools_dump,
            cached_session_message=self._cached_session_msg,
            cached_task_message=task_msg,
        )
        # Telemetry: we want to see prefix vs delta sizes when comparing
        # before/after KV-cache wins. Keep the payload tiny — this lands in
        # every iteration's event log.
        sizes = [len(m.content) for m in messages]
        kernel.events.append(
            kind=EventKind.LLM_REQUEST_SENT,
            actor="planner",
            task_id=task.task_id,
            payload={
                "messages_count": len(messages),
                "session_chars": sizes[0] if len(sizes) >= 1 else 0,
                "task_chars": sizes[1] if len(sizes) >= 2 else 0,
                "turn_chars": sizes[2] if len(sizes) >= 3 else 0,
                "total_chars": sum(sizes),
                "new_events_count": len(new_events),
                "events_index_before": since,
                "events_index_after": len(events),
            },
        )
        # Advance the per-task cursor so the next call sends only what comes
        # after this turn's LLM_REQUEST_SENT (which is itself appended above).
        self._last_event_index_sent[task.task_id] = len(events) + 1
        try:
            raw = self._llm.complete_json(
                messages=messages,
                json_schema=self._proposal_schema,
                schema_name="PlannerProposal",
            )
        except Exception as exc:  # noqa: BLE001
            raise PlannerError(f"LLM client error: {exc}") from exc

        kernel.events.append(
            kind=EventKind.LLM_PROPOSAL_RECEIVED,
            actor="planner",
            task_id=task.task_id,
            payload={"raw": raw},
        )
        coerced, coercion_note = _coerce_proposal_shape(raw)
        try:
            return PlannerProposal.model_validate(coerced)
        except Exception as exc:  # noqa: BLE001
            # We treat a malformed model output as a structured rejection so
            # the loop can retry. The exception is caught in ``run_task``
            # and turned into a rejection event + feedback for the model.
            raise _MalformedProposal(
                str(exc),
                raw=coerced,
                coercion_note=coercion_note,
            ) from exc

    @staticmethod
    def _malformed_to_decision(
        exc: _MalformedProposal,
        task: TaskSpec,
    ) -> PolicyDecision:
        """Synthesise a PolicyDecision so the next prompt sees the parse error."""
        action = ActionRequest(
            kind="tool_invocation",
            actor="planner",
            task_id=task.task_id,
            reason="(malformed proposal — see rejection)",
            input={},
        )
        message = (
            "Your last proposal could not be parsed. "
            "Every response must be a single JSON object with a non-empty "
            "``action`` field equal to one of: tool_invocation, "
            "ask_user_clarification, todo_branch, todo_complete, "
            "declare_task_done. "
            f"Validation error: {exc}"
        )
        if exc.coercion_note:
            message += f" Hint: {exc.coercion_note}"
        return PolicyDecision(
            decision=PolicyDecisionKind.DENY,
            action=action,
            rejection=PolicyRejection(
                reason_code="MALFORMED_PROPOSAL",
                message=message,
                rule_id="planner.parse",
            ),
            allowed_next_actions=[],
            forbidden_next_actions=[],
        )

    def _evaluate_proposal(
        self,
        proposal: PlannerProposal,
        *,
        todo: TodoTree,
        task: TaskSpec,
    ) -> PolicyDecision:
        action = _proposal_to_action(proposal, task)
        decision = self._policy.check(action, kernel=self._kernel, todo_tree=todo)
        self._kernel.events.append(
            kind=EventKind.POLICY_DECISION,
            actor="policy",
            task_id=task.task_id,
            payload={
                "action_kind": action.kind,
                "tool_name": action.tool_name,
                "decision": decision.decision.value,
                "reason_code": decision.rejection.reason_code if decision.rejection else None,
            },
        )
        return decision

    def _apply_action(
        self,
        proposal: PlannerProposal,
        *,
        task: TaskSpec,
        todo: TodoTree,
    ) -> _TaskDoneSignal | None:
        root = proposal.root

        if isinstance(root, ToolInvocationProposal):
            return self._invoke_tool(root, task=task, todo=todo)

        if isinstance(root, AskUserClarificationProposal):
            self._spawn_clarification(root, task=task, todo=todo)
            return None

        if isinstance(root, TodoBranchProposal):
            self._branch_todo(root, task=task, todo=todo)
            return None

        if isinstance(root, TodoCompleteProposal):
            self._complete_todo(root, task=task, todo=todo)
            return None

        if isinstance(root, DeclareTaskDoneProposal):
            self._declare_task_done(root, task=task)
            return _TASK_DONE

        raise PlannerError(f"unknown proposal kind: {root}")  # defensive

    # --- proposal handlers --------------------------------------------

    def _invoke_tool(
        self,
        proposal: ToolInvocationProposal,
        *,
        task: TaskSpec,
        todo: TodoTree,
    ) -> _TaskDoneSignal | None:
        kernel = self._kernel
        step_id = prefixed_id("step")
        invocation = ToolInvocation(
            invocation_id=prefixed_id("inv"),
            task_id=task.task_id,
            step_id=step_id,
            tool=ToolRef(name=proposal.tool_name, version=proposal.tool_version),
            input=proposal.input,
            provenance=InvocationProvenance(
                requested_by="planner",
                reason=proposal.reason,
                todo_node_id=proposal.target_todo_node_id,
            ),
        )
        kernel.events.append(
            kind=EventKind.TOOL_INVOCATION_STARTED,
            actor="planner",
            task_id=task.task_id,
            step_id=step_id,
            payload={"tool_name": proposal.tool_name, "reason": proposal.reason},
        )
        result = kernel.executor.invoke(invocation)
        ev_kind = (
            EventKind.TOOL_INVOCATION_COMPLETED
            if result.status == "success"
            else EventKind.TOOL_INVOCATION_FAILED
        )
        event_payload: dict[str, Any] = {
            "tool_name": proposal.tool_name,
            "status": result.status,
            "error_code": result.error.code if result.error else None,
            "output_keys": sorted(result.output.keys()),
        }
        compact_output = _compact_tool_output(proposal.tool_name, result.output)
        if compact_output:
            event_payload["output"] = compact_output
        evt = kernel.events.append(
            kind=ev_kind,
            actor=f"tool:{proposal.tool_name}",
            task_id=task.task_id,
            step_id=step_id,
            payload=event_payload,
        )
        if result.error is not None:
            kernel.state.mark_failure(task.task_id, result.error.code)
        self._motivation.dispatch(evt)

        if proposal.target_todo_node_id and todo.has(proposal.target_todo_node_id):
            target = todo.get(proposal.target_todo_node_id)
            if target.kind == TodoNodeKind.ACTION:
                todo.bind_step(target.node_id, step_id)
                if result.status == "success":
                    todo.set_status(target.node_id, TodoStatus.DONE)
                else:
                    todo.set_status(target.node_id, TodoStatus.BLOCKED)
        self._todo_store.save(todo)
        return self._maybe_finish_simple_find_task(
            proposal,
            result_output=result.output,
            result_status=result.status,
            task=task,
            todo=todo,
        )

    def _maybe_finish_simple_find_task(
        self,
        proposal: ToolInvocationProposal,
        *,
        result_output: dict[str, Any],
        result_status: str,
        task: TaskSpec,
        todo: TodoTree,
    ) -> _TaskDoneSignal | None:
        """End trivial file-location tasks after a proven ``find_files`` result.

        This is deliberately post-tool orchestration, not CLI command routing:
        the planner still chooses the discovery tool, but the runtime refuses
        to spend another LLM turn when the user's goal is only to locate a file
        and the tool already produced a clear path.
        """
        if proposal.tool_name != "find_files" or result_status != "success":
            return None
        if not _is_simple_file_find_task(task):
            return None

        summary = _find_files_completion_summary(proposal.input, result_output)
        if summary is None:
            return None

        with suppress(Exception):
            todo.set_status(todo.root_id, TodoStatus.DONE)
        evt = self._kernel.events.append(
            kind=EventKind.TASK_COMPLETED,
            actor="runtime",
            task_id=task.task_id,
            payload={
                "summary": summary,
                "reason": "deterministic_find_files_result",
            },
        )
        self._motivation.dispatch(evt)
        with suppress(Exception):
            self._kernel.state.transition_mode(task.task_id, RuntimeMode.TASK_DONE)
        self._todo_store.save(todo)
        return _TaskDoneSignal(summary=summary)

    def _spawn_clarification(
        self,
        proposal: AskUserClarificationProposal,
        *,
        task: TaskSpec,
        todo: TodoTree,
    ) -> None:
        kernel = self._kernel
        parent_id = proposal.target_todo_node_id or todo.root_id
        if not todo.has(parent_id):
            parent_id = todo.root_id
        node = todo.add_child(
            parent_id,
            kind=TodoNodeKind.CLARIFICATION,
            title=proposal.question[:80],
            description=proposal.reason,
            clarification_question=proposal.question,
            created_by="planner",
        )
        evt = kernel.events.append(
            kind=EventKind.TODO_BRANCHED,
            actor="planner",
            task_id=task.task_id,
            payload={
                "parent_id": parent_id,
                "node_id": node.node_id,
                "kind": "clarification",
                "question": proposal.question,
            },
        )
        self._motivation.dispatch(evt)
        # Pause via the same effect used by the ``ask_user`` tool path so
        # both routes preserve the pre-clarification operational state
        # (e.g. FAILURE_DIAGNOSIS_REQUIRED).
        state = kernel.state.ensure_task(task.task_id)
        _EFFECTS["pause_for_clarification"](
            _EffCtx(
                event=evt,
                task_state=state,
                kernel=kernel,
                rule_id="planner._spawn_clarification",
                rule_args={},
            )
        )
        self._todo_store.save(todo)

    def _branch_todo(
        self,
        proposal: TodoBranchProposal,
        *,
        task: TaskSpec,
        todo: TodoTree,
    ) -> None:
        kernel = self._kernel
        if not todo.has(proposal.parent_node_id):
            kernel.events.append(
                kind=EventKind.SYSTEM_MESSAGE,
                actor="planner",
                task_id=task.task_id,
                payload={"message": f"todo_branch: parent missing {proposal.parent_node_id}"},
            )
            return
        for spec in proposal.children:
            kind = TodoNodeKind(spec.kind)
            todo.add_child(
                proposal.parent_node_id,
                kind=kind,
                title=spec.title,
                description=spec.description,
                clarification_question=spec.clarification_question,
            )
        evt = kernel.events.append(
            kind=EventKind.TODO_BRANCHED,
            actor="planner",
            task_id=task.task_id,
            payload={
                "parent_id": proposal.parent_node_id,
                "child_count": len(proposal.children),
                "reason": proposal.reason,
            },
        )
        self._motivation.dispatch(evt)
        self._todo_store.save(todo)

    def _complete_todo(
        self,
        proposal: TodoCompleteProposal,
        *,
        task: TaskSpec,
        todo: TodoTree,
    ) -> None:
        kernel = self._kernel
        if not todo.has(proposal.node_id):
            return
        node = todo.get(proposal.node_id)
        # Completing a clarification node without an answer is allowed: the
        # planner may decide it no longer needs the answer.
        try:
            todo.set_status(proposal.node_id, TodoStatus.DONE)
        except ValueError:
            return
        evt = kernel.events.append(
            kind=EventKind.TODO_COMPLETED,
            actor="planner",
            task_id=task.task_id,
            payload={"node_id": node.node_id, "summary": proposal.summary},
        )
        self._motivation.dispatch(evt)
        self._todo_store.save(todo)

    def _declare_task_done(
        self,
        proposal: DeclareTaskDoneProposal,
        *,
        task: TaskSpec,
    ) -> None:
        kernel = self._kernel
        evt = kernel.events.append(
            kind=EventKind.TASK_COMPLETED,
            actor="planner",
            task_id=task.task_id,
            payload={"summary": proposal.summary},
        )
        self._motivation.dispatch(evt)
        # Defensive: the motivation rule sets mode to TASK_DONE; ensure it
        # stuck even if rules are turned off.
        with suppress(Exception):
            kernel.state.transition_mode(task.task_id, RuntimeMode.TASK_DONE)

    # --- helpers -------------------------------------------------------

    def _abort(self, task: TaskSpec, reason: str) -> PlannerOutcome:
        evt = self._kernel.events.append(
            kind=EventKind.TASK_FAILED,
            actor="runtime",
            task_id=task.task_id,
            payload={"reason": reason},
        )
        self._motivation.dispatch(evt)
        with suppress(Exception):
            self._kernel.state.transition_mode(task.task_id, RuntimeMode.BLOCKED)
        state = self._kernel.state.get_state(task.task_id)
        return PlannerOutcome(
            task_id=task.task_id,
            status=TaskStatus.ABORTED,
            iterations=state.iteration,
            last_decision=None,
            summary=None,
            aborted_reason=reason,
        )


def _proposal_to_action(proposal: PlannerProposal, task: TaskSpec) -> ActionRequest:
    root = proposal.root
    common: dict[str, Any] = {
        "task_id": task.task_id,
        "actor": "planner",
        "reason": root.reason,
        "todo_node_id": root.target_todo_node_id,
    }
    if isinstance(root, ToolInvocationProposal):
        return ActionRequest(
            kind="tool_invocation",
            tool_name=root.tool_name,
            tool_version=root.tool_version,
            input=dict(root.input),
            **common,
        )
    if isinstance(root, AskUserClarificationProposal):
        return ActionRequest(
            kind="ask_user_clarification",
            input={
                "question": root.question,
                "creates_clarification_node": root.creates_clarification_node,
            },
            **common,
        )
    if isinstance(root, TodoBranchProposal):
        return ActionRequest(
            kind="todo_branch",
            input={
                "parent_node_id": root.parent_node_id,
                "children": [c.model_dump() for c in root.children],
            },
            **common,
        )
    if isinstance(root, TodoCompleteProposal):
        return ActionRequest(
            kind="todo_complete",
            input={"node_id": root.node_id, "summary": root.summary},
            **common,
        )
    if isinstance(root, DeclareTaskDoneProposal):
        return ActionRequest(
            kind="declare_task_done",
            input={"summary": root.summary},
            **common,
        )
    raise PlannerError(f"unknown proposal kind: {root}")


def _summary_of(proposal: PlannerProposal) -> str | None:
    root = proposal.root
    if isinstance(root, DeclareTaskDoneProposal):
        return root.summary
    return None


def _compact_tool_output(tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
    if not output:
        return {}
    if tool_name == "find_files":
        matches = [
            {
                "relative_path": match.get("relative_path"),
                "size_bytes": match.get("size_bytes"),
            }
            for match in list(output.get("matches") or [])[:10]
            if isinstance(match, dict)
        ]
        return {
            "count": output.get("count", len(matches)),
            "truncated": bool(output.get("truncated", False)),
            "matches": matches,
        }
    if tool_name == "search_text":
        matches = [
            {
                "relative_path": match.get("relative_path"),
                "line_number": match.get("line_number"),
                "line": _truncate_text(str(match.get("line", "")), 300),
            }
            for match in list(output.get("matches") or [])[:10]
            if isinstance(match, dict)
        ]
        return {
            "count": output.get("count", len(matches)),
            "truncated": bool(output.get("truncated", False)),
            "matches": matches,
            "searched_files": output.get("searched_files"),
            "skipped_files": output.get("skipped_files"),
        }
    if tool_name == "read_file":
        content = str(output.get("content", ""))
        return {
            "path": output.get("path"),
            "bytes_read": output.get("bytes_read"),
            "start_line": output.get("start_line"),
            "end_line": output.get("end_line"),
            "total_lines": output.get("total_lines"),
            "end_of_file": output.get("end_of_file"),
            "truncated": bool(output.get("truncated", False)),
            "content_excerpt_truncated": len(content) > 4000,
            "content_excerpt": _truncate_text(content, 4000),
            "sha256": output.get("sha256"),
            "file_sha256": output.get("file_sha256"),
            "receipt_id": output.get("receipt_id"),
        }
    if tool_name == "copy_file":
        return {
            "source_path": output.get("source_path"),
            "destination_path": output.get("destination_path"),
            "bytes_source": output.get("bytes_source"),
            "bytes_written": output.get("bytes_written"),
            "appended_bytes": output.get("appended_bytes"),
            "sha256_source": output.get("sha256_source"),
            "sha256_after": output.get("sha256_after"),
            "overwrote": output.get("overwrote"),
            "backup_artifact_id": output.get("backup_artifact_id"),
            "rollback_plan_id": output.get("rollback_plan_id"),
            "receipt_id": output.get("receipt_id"),
        }
    if tool_name == "verify_file":
        results = [
            {
                "check": r.get("check"),
                "passed": r.get("passed"),
                "skipped": r.get("skipped", False),
                "error": _truncate_text(str(r.get("error", "")), 300) if r.get("error") else None,
                "exit_code": r.get("exit_code"),
            }
            for r in list(output.get("results") or [])
            if isinstance(r, dict)
        ]
        return {
            "path": output.get("path"),
            "file_type": output.get("file_type"),
            "checks_run": output.get("checks_run"),
            "overall_passed": output.get("overall_passed"),
            "results": results,
        }
    if tool_name == "read_task_log":
        return {
            "task_id": output.get("task_id"),
            "closed": output.get("closed"),
            "total_entries": output.get("total_entries"),
            "returned_entries": output.get("returned_entries"),
            "truncated": output.get("truncated"),
            "summary": output.get("summary"),
        }
    return {
        key: _truncate_text(value, 500) if isinstance(value, str) else value
        for key, value in output.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


_ASCII_FIND_WORDS = (
    "find",
    "locate",
    "search",
)
_CYRILLIC_FIND_WORDS = (
    "найд",
    "найти",
    "ищи",
    "отыщи",
)
_ASCII_READ_OR_OPEN_WORDS = (
    "open",
    "read",
    "show",
    "display",
    "inspect",
)
_CYRILLIC_READ_OR_OPEN_WORDS = (
    "откро",
    "прочит",
    "прочти",
    "покаж",
    "вывед",
    "изучи",
    "посмотри",
)


def _is_simple_file_find_task(task: TaskSpec) -> bool:
    text = f"{task.raw_request}\n{task.goal}".casefold()
    ascii_words = set(re.findall(r"[a-z0-9_]+", text))
    has_read_or_open = any(word in ascii_words for word in _ASCII_READ_OR_OPEN_WORDS) or any(
        word in text for word in _CYRILLIC_READ_OR_OPEN_WORDS
    )
    if has_read_or_open:
        return False
    return any(word in ascii_words for word in _ASCII_FIND_WORDS) or any(
        word in text for word in _CYRILLIC_FIND_WORDS
    )


def _find_files_completion_summary(
    tool_input: dict[str, Any],
    output: dict[str, Any],
) -> str | None:
    matches = [
        str(match["relative_path"])
        for match in output.get("matches", [])
        if isinstance(match, dict) and match.get("relative_path")
    ]
    if not matches:
        return None

    requested = _requested_file_name(tool_input)
    primary = _select_primary_find_match(requested, matches)
    if primary is None:
        return None

    label = requested or primary.rsplit("/", 1)[-1]
    alternatives = [path for path in matches if path != primary]
    if not alternatives:
        return f"Found {label}: {primary}"

    shown_alternatives = ", ".join(alternatives[:5])
    if len(alternatives) > 5:
        shown_alternatives += f", ... +{len(alternatives) - 5} more"
    return f"Found {label}: {primary} (also: {shown_alternatives})"


def _requested_file_name(tool_input: dict[str, Any]) -> str:
    value = str(tool_input.get("query") or tool_input.get("glob") or "").strip()
    if not value or any(ch in value for ch in "*?[]"):
        return ""
    return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _select_primary_find_match(requested: str, matches: list[str]) -> str | None:
    if not requested:
        return matches[0] if len(matches) == 1 else None

    requested_folded = requested.casefold()
    exact_root = [path for path in matches if path.casefold() == requested_folded]
    if len(exact_root) == 1:
        return exact_root[0]

    exact_name = [
        path
        for path in matches
        if path.replace("\\", "/").rsplit("/", 1)[-1].casefold() == requested_folded
    ]
    if len(exact_name) == 1:
        return exact_name[0]

    root_name = [path for path in exact_name if "/" not in path.replace("\\", "/")]
    if len(root_name) == 1:
        return root_name[0]

    return None


_TASK_DONE = _TaskDoneSignal()


class _MalformedProposal(PlannerError):
    def __init__(
        self,
        message: str,
        *,
        raw: dict[str, Any],
        coercion_note: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw = raw
        self.coercion_note = coercion_note

    def __str__(self) -> str:  # pragma: no cover
        return f"{super().__str__()}: {json.dumps(self.raw, ensure_ascii=False)[:300]}"


def _coerce_proposal_shape(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Best-effort recovery of a missing ``action`` discriminator.

    Some local models omit the discriminator while emitting an otherwise
    well-shaped tool call. We only repair that narrow case because it is still
    routed through ToolRegistry/PolicyEngine. Bare ``question`` payloads must
    stay malformed; otherwise the parser itself becomes a user-contact bypass.
    """
    if not isinstance(raw, dict):
        return raw, None
    if "action" in raw and raw["action"]:
        return raw, None
    patched = dict(raw)
    note: str | None = None
    if "tool_name" in raw and "tool_version" in raw:
        patched["action"] = "tool_invocation"
        note = "inferred action='tool_invocation' from presence of tool_name/tool_version"
    return patched, note
