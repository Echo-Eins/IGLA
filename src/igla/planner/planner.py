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
from dataclasses import dataclass
from typing import Any

from ..config import IglaSettings
from ..ids import prefixed_id
from ..kernel.kernel import Kernel
from ..motivation.cycle import MotivationCycle
from ..policies.engine import PolicyEngine
from ..protocol.event import EventKind
from ..protocol.invocation import (
    InvocationProvenance,
    ToolInvocation,
    ToolRef,
)
from ..protocol.policy import ActionRequest, PolicyDecision, PolicyDecisionKind
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
from .llm_client import LLMClient
from .prompts import build_proposal_messages, build_proposal_schema, render_event_tail


@dataclass
class PlannerOutcome:
    task_id: str
    status: TaskStatus
    iterations: int
    last_decision: PolicyDecision | None
    summary: str | None
    aborted_reason: str | None = None


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
            proposal = self._ask_planner(task=task, todo=todo, last_decision=last_decision)
            decision = self._evaluate_proposal(proposal, todo=todo, task=task)
            last_decision = decision

            if decision.is_deny:
                rejections = kernel.state.record_rejection(
                    task.task_id,
                    reason_code=(decision.rejection.reason_code if decision.rejection else "DENIED"),
                    message=(decision.rejection.message if decision.rejection else "policy deny"),
                )
                kernel.events.append(
                    kind=EventKind.POLICY_REJECTION,
                    actor="policy",
                    task_id=task.task_id,
                    payload={
                        "reason_code": decision.rejection.reason_code if decision.rejection else None,
                        "message": decision.rejection.message if decision.rejection else None,
                        "rule_id": decision.rejection.rule_id if decision.rejection else None,
                        "action_kind": decision.action.kind,
                        "tool_name": decision.action.tool_name,
                    },
                )
                if rejections >= self._settings.planner.max_consecutive_rejections:
                    return self._abort(task, "MAX_CONSECUTIVE_REJECTIONS")
                continue

            kernel.state.reset_rejections(task.task_id)
            outcome = self._apply_action(proposal, task=task, todo=todo)
            if outcome is _TASK_DONE:
                return PlannerOutcome(
                    task_id=task.task_id,
                    status=TaskStatus.DONE,
                    iterations=kernel.state.get_state(task.task_id).iteration,
                    last_decision=decision,
                    summary=_summary_of(proposal),
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
        tail = render_event_tail(events, limit=12)
        tools = [
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

        messages = build_proposal_messages(
            task=task,
            runtime=runtime_snapshot,
            todo=snapshot,
            last_decision=last_decision,
            last_event_log_tail=tail,
            available_tools=tools,
        )
        kernel.events.append(
            kind=EventKind.LLM_REQUEST_SENT,
            actor="planner",
            task_id=task.task_id,
            payload={"messages_count": len(messages)},
        )
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
        try:
            return PlannerProposal.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            # We treat a malformed model output as a rejection so the loop
            # can retry without crashing the chat session.
            raise _MalformedProposal(str(exc), raw=raw) from exc

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
    ) -> object:
        kernel = self._kernel
        root = proposal.root

        if isinstance(root, ToolInvocationProposal):
            self._invoke_tool(root, task=task, todo=todo)
            return None

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
    ) -> None:
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
        evt = kernel.events.append(
            kind=ev_kind,
            actor=f"tool:{proposal.tool_name}",
            task_id=task.task_id,
            step_id=step_id,
            payload={
                "tool_name": proposal.tool_name,
                "status": result.status,
                "error_code": result.error.code if result.error else None,
                "output_keys": sorted(result.output.keys()),
            },
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
        from ..motivation.effects import EFFECTS as _EFFECTS
        from ..motivation.effects import EffectContext as _EffCtx

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
        try:
            kernel.state.transition_mode(task.task_id, RuntimeMode.TASK_DONE)
        except Exception:
            pass

    # --- helpers -------------------------------------------------------

    def _abort(self, task: TaskSpec, reason: str) -> PlannerOutcome:
        evt = self._kernel.events.append(
            kind=EventKind.TASK_FAILED,
            actor="runtime",
            task_id=task.task_id,
            payload={"reason": reason},
        )
        self._motivation.dispatch(evt)
        try:
            self._kernel.state.transition_mode(task.task_id, RuntimeMode.BLOCKED)
        except Exception:
            pass
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


_TASK_DONE = object()


class _MalformedProposal(PlannerError):
    def __init__(self, message: str, *, raw: dict[str, Any]) -> None:
        super().__init__(message)
        self.raw = raw

    def __str__(self) -> str:  # pragma: no cover
        return f"{super().__str__()}: {json.dumps(self.raw)[:300]}"
