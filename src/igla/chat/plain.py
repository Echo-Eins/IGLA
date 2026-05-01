"""Plain text CLI for debugging IGLA behavior.

This is intentionally boring: no Rich panels, no box drawing, no colors. The
output is easy to copy into bug reports and includes the task event timeline.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..config import IglaSettings
from ..console_io import safe_print, safe_readline
from ..ids import prefixed_id
from ..kernel.kernel import Kernel
from ..motivation.cycle import MotivationCycle
from ..motivation.rule import load_rules
from ..planner.llm_client import LLMChatMessage, LLMClient
from ..planner.planner import Planner, PlannerOutcome
from ..policies.constitution import load_constitution
from ..policies.engine import PolicyContext, PolicyEngine
from ..protocol.event import EventRecord
from ..protocol.task import TaskSpec, TaskStatus
from ..protocol.todo import TodoStatus
from ..state_reset import StateResetError, reset_workspace_state
from ..todo.render import render_text
from ..todo.store import TodoStore
from ..todo.tree import TodoTree
from ..tools.builtin import (
    AskUserTool,
    CopyFileTool,
    FindFilesTool,
    ListDirTool,
    NoopObserveTool,
    PatchFileTool,
    ReadFileTool,
    ReadTaskLogTool,
    RestoreFileTool,
    SearchTextTool,
    VerifyFileTool,
)


class _RtlogLLMClient:
    """Wraps any LLMClient and prints a compact real-time log of each turn."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner

    def complete_json(
        self,
        *,
        messages: Iterable[LLMChatMessage],
        json_schema: dict[str, Any] | None,
        schema_name: str = "PlannerProposal",
    ) -> dict[str, Any]:
        msgs = list(messages)
        non_sys = [m for m in msgs if m.role != "system"]
        total_chars = sum(len(m.content) for m in msgs)
        safe_print(f"\n{'─' * 60}")
        safe_print(f"[→ LLM] {len(msgs)} messages, {total_chars} chars total")
        if non_sys:
            last = non_sys[-1]
            content = last.content
            if len(content) > 1500:
                content = content[:1500] + f"\n... [{len(last.content) - 1500} chars truncated]"
            safe_print(f"[→ TURN]:\n{content}")

        raw = self._inner.complete_json(
            messages=msgs,
            json_schema=json_schema,
            schema_name=schema_name,
        )

        action = raw.get("action", "?")
        tool = raw.get("tool_name", "")
        inp = raw.get("input") or {}
        reason = str(raw.get("reason", ""))
        if tool:
            params_str = json.dumps(inp, ensure_ascii=False, separators=(",", ":"))
            if len(params_str) > 600:
                params_str = params_str[:600] + "..."
            safe_print(f"[← MODEL] action={action}  tool={tool}")
            safe_print(f"[← PARAMS] {params_str}")
        else:
            safe_print(f"[← MODEL] action={action}")
        if reason:
            r = reason[:200] + "..." if len(reason) > 200 else reason
            safe_print(f"[← REASON] {r}")
        return raw

    def close(self) -> None:
        self._inner.close()


@dataclass
class PlainTranscript:
    entries: list[dict[str, str]] = field(default_factory=list)

    def add(self, role: str, text: str) -> None:
        self.entries.append({"role": role, "text": text})


class _PlainAskUserChannel:
    def __init__(self, transcript: PlainTranscript) -> None:
        self._transcript = transcript

    def ask(self, *, question: str, prompt_label: str | None = None) -> str:
        del prompt_label
        safe_print(f"QUESTION: {question}")
        self._transcript.add("igla", question)
        answer = safe_readline("you> ")
        self._transcript.add("user", answer)
        return answer


class PlainCLI:
    def __init__(self, *, settings: IglaSettings, llm: LLMClient, rtlog: bool = False) -> None:
        self._settings = settings
        self._llm = _RtlogLLMClient(llm) if rtlog else llm
        self._transcript = PlainTranscript()
        self._wire_runtime()

    def _wire_runtime(self) -> None:
        self._kernel = Kernel(self._settings)
        self._todo_store = TodoStore(self._settings.paths.todo_dir)
        self._planner = self._build_planner(self._llm)

    def run_loop(self) -> None:
        safe_print("IGLA plain CLI")
        safe_print(f"workspace: {self._settings.paths.workspace}")
        safe_print("commands: /quit /state /todo /reset-state")
        while True:
            text = safe_readline("> ").strip()
            if not text:
                continue
            if text.startswith("/"):
                if self._handle_command(text):
                    return
                continue
            self.run_once(text)

    def run_once(self, raw_request: str) -> PlannerOutcome:
        self._transcript.add("user", raw_request)
        task = TaskSpec(
            task_id=prefixed_id("task"),
            raw_request=raw_request,
            goal=raw_request,
            status=TaskStatus.READY,
            created_at=self._kernel.clock.now(),
        )
        todo = TodoTree(task.task_id, self._kernel.clock)
        todo.create_root(title=raw_request[:120], description=raw_request)
        self._todo_store.save(todo)

        safe_print(f"TASK {task.task_id}")
        safe_print(f"GOAL {raw_request}")

        try:
            outcome = self._planner.run_task(task, todo)
            while outcome.status is TaskStatus.NEEDS_USER_CLARIFICATION:
                question = self._latest_clarifying_question(todo)
                safe_print(f"QUESTION: {question or '(missing clarification question)'}")
                answer = safe_readline("you> ")
                self._transcript.add("user", answer)
                outcome = self._planner.handle_user_input(task=task, todo=todo, text=answer)
        except Exception as exc:  # noqa: BLE001 - plain debug boundary
            safe_print("STATUS crashed")
            safe_print(f"ERROR {exc.__class__.__name__}: {exc}")
            raise

        self._render_finish(outcome, todo)
        return outcome

    def _build_planner(self, llm: LLMClient) -> Planner:
        ask_tool = AskUserTool(channel=_PlainAskUserChannel(self._transcript))
        self._kernel.registry.register_many(
            [
                ask_tool,
                FindFilesTool(workspace_root=str(self._settings.paths.workspace)),
                ListDirTool(workspace_root=str(self._settings.paths.workspace)),
                ReadFileTool(
                    workspace_root=str(self._settings.paths.workspace),
                    receipts=self._kernel.receipts,
                ),
                SearchTextTool(workspace_root=str(self._settings.paths.workspace)),
                CopyFileTool(
                    workspace_root=str(self._settings.paths.workspace),
                    receipts=self._kernel.receipts,
                    rollback=self._kernel.rollback,
                ),
                PatchFileTool(
                    workspace_root=str(self._settings.paths.workspace),
                    receipts=self._kernel.receipts,
                    rollback=self._kernel.rollback,
                ),
                RestoreFileTool(
                    workspace_root=str(self._settings.paths.workspace),
                    receipts=self._kernel.receipts,
                    rollback=self._kernel.rollback,
                ),
                VerifyFileTool(
                    workspace_root=str(self._settings.paths.workspace),
                    receipts=self._kernel.receipts,
                ),
                ReadTaskLogTool(work_log=self._kernel.work_log),

                NoopObserveTool(),
            ]
        )
        constitution = load_constitution(self._settings.paths.constitution_file)
        policy = PolicyEngine(
            constitution,
            PolicyContext(
                iteration_limit=self._settings.planner.max_iterations_per_task,
                clarification_depth_limit=self._settings.planner.max_clarification_depth,
                consecutive_rejections_limit=self._settings.planner.max_consecutive_rejections,
            ),
        )
        motivation = MotivationCycle(load_rules(self._settings.paths.motivation_file), self._kernel)
        return Planner(
            kernel=self._kernel,
            settings=self._settings,
            policy=policy,
            motivation=motivation,
            todo_store=self._todo_store,
            llm=llm,
        )

    def _render_finish(self, outcome: PlannerOutcome, todo: TodoTree) -> None:
        safe_print(f"STATUS {outcome.status.value}")
        safe_print(f"TASK {outcome.task_id}")
        safe_print(f"ITERATIONS {outcome.iterations}")
        if outcome.summary:
            safe_print(f"SUMMARY {outcome.summary}")
        if outcome.aborted_reason:
            safe_print(f"REASON {outcome.aborted_reason}")
        safe_print("TODO")
        safe_print(render_text(todo.snapshot(), with_ids=True))
        safe_print("EVENTS")
        for event in self._kernel.events.list_by_task(outcome.task_id):
            safe_print(_render_event(event))

    def _handle_command(self, line: str) -> bool:
        command, _, rest = line.partition(" ")
        command = command.lower()
        if command in {"/quit", "/exit"}:
            return True
        if command == "/state":
            safe_print(f"workspace: {self._settings.paths.workspace}")
            safe_print(f"state_dir: {self._settings.paths.state_dir}")
            safe_print(f"events: {sum(1 for _ in self._kernel.events.iter_all())}")
            tools = ", ".join(m.name for m in self._kernel.registry.list_tools())
            safe_print(f"tools: {tools}")
            return False
        if command == "/reset-state":
            try:
                result = reset_workspace_state(
                    workspace=self._settings.paths.workspace,
                    state_dir=self._settings.paths.state_dir,
                )
            except StateResetError as exc:
                safe_print(f"ERROR {exc}")
                return False
            self._wire_runtime()
            if result.removed:
                safe_print(f"STATE_RESET removed {result.state_dir}")
            else:
                safe_print(f"STATE_RESET already_absent {result.state_dir}")
            return False
        if command == "/todo":
            task_id = rest.strip() or self._last_task_id()
            if not task_id:
                safe_print("no tasks")
                return False
            snapshot = self._todo_store.load_snapshot(task_id)
            if snapshot is None:
                safe_print(f"task not found: {task_id}")
                return False
            safe_print(render_text(snapshot, with_ids=True))
            return False
        safe_print(f"unknown command: {command}")
        return False

    def _last_task_id(self) -> str | None:
        ids = self._todo_store.all_task_ids()
        return ids[-1] if ids else None

    @staticmethod
    def _latest_clarifying_question(todo: TodoTree) -> str | None:
        nodes = [node for node in todo.all_nodes() if node.status is TodoStatus.CLARIFYING]
        if not nodes:
            return None
        nodes.sort(key=lambda node: node.updated_at, reverse=True)
        return nodes[0].clarification_question or nodes[0].title


def _render_event(event: EventRecord) -> str:
    payload = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    step = f" step={event.step_id}" if event.step_id else ""
    return f"{event.kind.value} actor={event.actor}{step} payload={payload}"
