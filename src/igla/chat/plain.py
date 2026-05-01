"""Plain text CLI for debugging IGLA behavior.

This is intentionally boring: no Rich panels, no box drawing, no colors. The
output is easy to copy into bug reports and includes the task event timeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..config import IglaSettings
from ..console_io import safe_readline
from ..ids import prefixed_id
from ..kernel.kernel import Kernel
from ..motivation.cycle import MotivationCycle
from ..motivation.rule import load_rules
from ..planner.llm_client import LLMClient
from ..planner.planner import Planner, PlannerOutcome
from ..policies.constitution import load_constitution
from ..policies.engine import PolicyContext, PolicyEngine
from ..protocol.event import EventRecord
from ..protocol.task import TaskSpec, TaskStatus
from ..protocol.todo import TodoStatus
from ..todo.render import render_text
from ..todo.store import TodoStore
from ..todo.tree import TodoTree
from ..tools.builtin import (
    AskUserTool,
    FindFilesTool,
    NoopObserveTool,
    ReadFileTool,
    SearchTextTool,
)


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
        print(f"QUESTION: {question}")
        self._transcript.add("igla", question)
        answer = safe_readline("you> ")
        self._transcript.add("user", answer)
        return answer


class PlainCLI:
    def __init__(self, *, settings: IglaSettings, llm: LLMClient) -> None:
        self._settings = settings
        self._transcript = PlainTranscript()
        self._kernel = Kernel(settings)
        self._todo_store = TodoStore(settings.paths.todo_dir)
        self._planner = self._build_planner(llm)

    def run_loop(self) -> None:
        print("IGLA plain CLI")
        print(f"workspace: {self._settings.paths.workspace}")
        print("commands: /quit /state /todo")
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

        print(f"TASK {task.task_id}")
        print(f"GOAL {raw_request}")

        try:
            outcome = self._planner.run_task(task, todo)
            while outcome.status is TaskStatus.NEEDS_USER_CLARIFICATION:
                question = self._latest_clarifying_question(todo)
                print(f"QUESTION: {question or '(missing clarification question)'}")
                answer = safe_readline("you> ")
                self._transcript.add("user", answer)
                outcome = self._planner.handle_user_input(task=task, todo=todo, text=answer)
        except Exception as exc:  # noqa: BLE001 - plain debug boundary
            print("STATUS crashed")
            print(f"ERROR {exc.__class__.__name__}: {exc}")
            raise

        self._render_finish(outcome, todo)
        return outcome

    def _build_planner(self, llm: LLMClient) -> Planner:
        ask_tool = AskUserTool(channel=_PlainAskUserChannel(self._transcript))
        self._kernel.registry.register_many(
            [
                ask_tool,
                FindFilesTool(workspace_root=str(self._settings.paths.workspace)),
                ReadFileTool(
                    workspace_root=str(self._settings.paths.workspace),
                    receipts=self._kernel.receipts,
                ),
                SearchTextTool(workspace_root=str(self._settings.paths.workspace)),
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
        print(f"STATUS {outcome.status.value}")
        print(f"TASK {outcome.task_id}")
        print(f"ITERATIONS {outcome.iterations}")
        if outcome.summary:
            print(f"SUMMARY {outcome.summary}")
        if outcome.aborted_reason:
            print(f"REASON {outcome.aborted_reason}")
        print("TODO")
        print(render_text(todo.snapshot(), with_ids=True))
        print("EVENTS")
        for event in self._kernel.events.list_by_task(outcome.task_id):
            print(_render_event(event))

    def _handle_command(self, line: str) -> bool:
        command, _, rest = line.partition(" ")
        command = command.lower()
        if command in {"/quit", "/exit"}:
            return True
        if command == "/state":
            print(f"workspace: {self._settings.paths.workspace}")
            print(f"events: {sum(1 for _ in self._kernel.events.iter_all())}")
            tools = ", ".join(m.name for m in self._kernel.registry.list_tools())
            print(f"tools: {tools}")
            return False
        if command == "/todo":
            task_id = rest.strip() or self._last_task_id()
            if not task_id:
                print("no tasks")
                return False
            snapshot = self._todo_store.load_snapshot(task_id)
            if snapshot is None:
                print(f"task not found: {task_id}")
                return False
            print(render_text(snapshot, with_ids=True))
            return False
        print(f"unknown command: {command}")
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
