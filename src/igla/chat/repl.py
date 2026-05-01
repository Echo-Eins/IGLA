"""Chat REPL.

Flow:

1. Read user intent from stdin (one line, but multi-line via ``\\`` line
   continuation is supported).
2. Build a ``TaskSpec`` and an empty ``TodoTree`` rooted at the goal.
3. Drive the planner until it either finishes or asks for clarification.
4. If ``NEEDS_USER_CLARIFICATION``: show the open clarifying question, read
   the user's answer, feed it back via ``handle_user_input``, repeat.
5. On task end: print a compact summary and the TODO tree.

Hard rules:
* Only the planner-driven path emits side effects. The REPL does not call
  tools directly.
* All mutations are persisted to ``.igla/`` so the session can be inspected
  with simple ``cat`` / ``less`` after the fact.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

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
from ..protocol.task import TaskSpec, TaskStatus
from ..protocol.todo import TodoStatus
from ..todo.render import render_text
from ..todo.store import TodoStore
from ..todo.tree import TodoTree
from ..tools.builtin import (
    AskUserChannel,
    AskUserTool,
    FindFilesTool,
    ListDirTool,
    NoopObserveTool,
    PatchFileTool,
    ReadFileTool,
    RestoreFileTool,
    SearchTextTool,
)


@dataclass
class ChatTranscript:
    """In-memory transcript of the current chat session.

    Persistent transcripts will live in EventStore; this dataclass simply
    keeps the most recent messages for prompt context.
    """

    entries: list[dict[str, str]] = field(default_factory=list)

    def add(self, role: str, text: str) -> None:
        self.entries.append({"role": role, "text": text})

    def tail(self, n: int = 10) -> list[dict[str, str]]:
        return self.entries[-n:]


def _prompt_line(console: Console, prompt_markup: str) -> str:
    """Read one line, surviving locale / decoding glitches.

    Tries the rich prompt first (which gives nice ANSI handling); on any
    UnicodeError we fall back to ``safe_readline`` which reads bytes from
    ``sys.stdin.buffer`` and decodes with ``errors="replace"``. EOF and
    KeyboardInterrupt return the empty string here; the REPL converts that
    into "no input" instead of crashing the session.
    """
    try:
        return console.input(prompt_markup)
    except (EOFError, KeyboardInterrupt):
        raise
    except UnicodeError:
        # Rich is unable to decode the byte stream — degrade gracefully.
        try:
            console.print(prompt_markup, end="")
        except Exception:  # noqa: BLE001
            pass
        return safe_readline()


class _RichAskUserChannel:
    """Channel implementation backed by the chat console."""

    def __init__(self, console: Console, transcript: ChatTranscript) -> None:
        self._console = console
        self._transcript = transcript

    def ask(self, *, question: str, prompt_label: str | None = None) -> str:
        self._console.print(Panel(question, title="ИГЛА спрашивает", border_style="cyan"))
        self._transcript.add("igla", question)
        try:
            answer = _prompt_line(self._console, "[bold]you[/bold]> ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        self._transcript.add("user", answer)
        return answer


class ChatREPL:
    """Construct a fully wired runtime + chat loop.

    Parameters
    ----------
    settings : IglaSettings
    llm : LLMClient
    console : Console | None
        Defaults to a fresh ``rich.console.Console``.
    """

    def __init__(
        self,
        *,
        settings: IglaSettings,
        llm: LLMClient,
        console: Console | None = None,
    ) -> None:
        self._settings = settings
        self._console = console or Console()
        self._transcript = ChatTranscript()
        self._kernel = Kernel(settings)
        self._todo_store = TodoStore(settings.paths.todo_dir)

        # Build tools that need the chat I/O.
        ask_channel: AskUserChannel = _RichAskUserChannel(self._console, self._transcript)
        ask_tool = AskUserTool(channel=ask_channel)
        find_tool = FindFilesTool(workspace_root=str(settings.paths.workspace))
        list_dir_tool = ListDirTool(workspace_root=str(settings.paths.workspace))
        read_tool = ReadFileTool(
            workspace_root=str(settings.paths.workspace),
            receipts=self._kernel.receipts,
        )
        search_tool = SearchTextTool(workspace_root=str(settings.paths.workspace))
        patch_tool = PatchFileTool(
            workspace_root=str(settings.paths.workspace),
            receipts=self._kernel.receipts,
            rollback=self._kernel.rollback,
        )
        restore_tool = RestoreFileTool(
            workspace_root=str(settings.paths.workspace),
            receipts=self._kernel.receipts,
            rollback=self._kernel.rollback,
        )
        observe_tool = NoopObserveTool()
        self._kernel.registry.register_many(
            [
                ask_tool,
                find_tool,
                list_dir_tool,
                read_tool,
                search_tool,
                patch_tool,
                restore_tool,
                observe_tool,
            ]
        )

        constitution = load_constitution(settings.paths.constitution_file)
        policy = PolicyEngine(
            constitution,
            PolicyContext(
                iteration_limit=settings.planner.max_iterations_per_task,
                clarification_depth_limit=settings.planner.max_clarification_depth,
                consecutive_rejections_limit=settings.planner.max_consecutive_rejections,
            ),
        )
        rules = load_rules(settings.paths.motivation_file)
        motivation = MotivationCycle(rules, self._kernel)

        self._planner = Planner(
            kernel=self._kernel,
            settings=settings,
            policy=policy,
            motivation=motivation,
            todo_store=self._todo_store,
            llm=llm,
        )

    # --- public API -----------------------------------------------------

    def banner(self) -> None:
        version_line = f"workspace: {self._settings.paths.workspace}"
        body = Text.from_markup(
            "[bold]ИГЛА[/bold] — Iterative-Generated Local Analysis.\n"
            "Действия, не ответы. Пиши намерение — ИГЛА построит TODO и выполнит шаги.\n"
            "Команды: [cyan]/quit[/cyan] [cyan]/state[/cyan] [cyan]/todo[/cyan] [cyan]/help[/cyan]"
        )
        self._console.print(Panel(body, title="IGLA", subtitle=version_line, border_style="green"))

    def run(self) -> None:
        self.banner()
        while True:
            try:
                line = _prompt_line(self._console, "[bold green]> [/bold green]")
            except (EOFError, KeyboardInterrupt):
                self._console.print("\n[dim]session ended[/dim]")
                return
            text = line.strip()
            if not text:
                continue
            if text.startswith("/"):
                if self._handle_command(text):
                    return
                continue
            self._transcript.add("user", text)
            self._run_one_intent(text)

    # --- intent handling -----------------------------------------------

    def _run_one_intent(self, raw_request: str) -> None:
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

        self._console.print(
            Panel(
                Text(f"goal: {raw_request}\ntask_id: {task.task_id}", style="white"),
                title="task",
                border_style="blue",
            )
        )
        try:
            outcome = self._planner.run_task(task, todo)
        except Exception as exc:  # noqa: BLE001 — protect the chat session
            import traceback as _tb

            self._console.print(
                Panel(
                    f"{exc}\n\n[dim]{_tb.format_exc()}[/dim]",
                    title="planner crashed",
                    border_style="red",
                )
            )
            return
        self._handle_outcome(task, todo, outcome)

    def _handle_outcome(
        self,
        task: TaskSpec,
        todo: TodoTree,
        outcome: PlannerOutcome,
    ) -> None:
        while outcome.status is TaskStatus.NEEDS_USER_CLARIFICATION:
            question_node = self._latest_clarifying(todo)
            if question_node is None:
                # Defensive: planner asked for clarification but no node?
                self._console.print(
                    Panel(
                        "Planner ожидает ответа, но не нашлось CLARIFYING-узла. Прерываю задачу.",
                        title="warn",
                        border_style="yellow",
                    )
                )
                return
            self._console.print(
                Panel(
                    question_node.clarification_question or question_node.title,
                    title=f"вопрос {question_node.node_id[-6:]}",
                    border_style="cyan",
                )
            )
            try:
                answer = _prompt_line(self._console, "[bold]you[/bold]> ")
            except (EOFError, KeyboardInterrupt):
                self._console.print("\n[dim]задача прервана[/dim]")
                return
            self._transcript.add("user", answer)
            outcome = self._planner.handle_user_input(task=task, todo=todo, text=answer)

        self._render_finish(outcome, todo)

    def _latest_clarifying(self, todo: TodoTree):
        nodes = [n for n in todo.all_nodes() if n.status is TodoStatus.CLARIFYING]
        if not nodes:
            return None
        nodes.sort(key=lambda n: n.updated_at, reverse=True)
        return nodes[0]

    def _render_finish(self, outcome: PlannerOutcome, todo) -> None:
        title = {
            TaskStatus.DONE: "✓ done",
            TaskStatus.ABORTED: "✕ aborted",
            TaskStatus.BLOCKED: "● blocked",
        }.get(outcome.status, outcome.status.value)
        body_lines = [
            f"task: {outcome.task_id}",
            f"iterations: {outcome.iterations}",
        ]
        if outcome.summary:
            body_lines.append(f"summary: {outcome.summary}")
        if outcome.aborted_reason:
            body_lines.append(f"reason: {outcome.aborted_reason}")
        body_lines.append("")
        body_lines.append(render_text(todo.snapshot()))
        self._console.print(
            Panel(
                "\n".join(body_lines),
                title=title,
                border_style="green" if outcome.status is TaskStatus.DONE else "yellow",
            )
        )

    # --- /commands -----------------------------------------------------

    def _handle_command(self, line: str) -> bool:
        cmd, _, rest = line.partition(" ")
        cmd = cmd.lower()
        if cmd in {"/quit", "/exit"}:
            return True
        if cmd == "/help":
            self._print_help()
            return False
        if cmd == "/state":
            self._print_state()
            return False
        if cmd == "/todo":
            self._print_todo(rest.strip() or None)
            return False
        self._console.print(f"[red]unknown command:[/red] {cmd}")
        return False

    def _print_help(self) -> None:
        self._console.print(
            Panel(
                "/quit — выйти\n"
                "/state — показать сводку runtime\n"
                "/todo [task_id] — показать TODO для задачи (последняя по умолчанию)\n"
                "/help — это сообщение",
                title="help",
                border_style="magenta",
            )
        )

    def _print_state(self) -> None:
        events_count = sum(1 for _ in self._kernel.events.iter_all())
        registered = ", ".join(m.name for m in self._kernel.registry.list_tools())
        self._console.print(
            Panel(
                f"workspace: {self._settings.paths.workspace}\n"
                f"events: {events_count}\n"
                f"registered tools: {registered or '(none)'}",
                title="runtime state",
                border_style="blue",
            )
        )

    def _print_todo(self, task_id: str | None) -> None:
        if task_id is None:
            tasks = self._todo_store.all_task_ids()
            if not tasks:
                self._console.print("[dim]нет задач[/dim]")
                return
            task_id = tasks[-1]
        snapshot = self._todo_store.load_snapshot(task_id)
        if snapshot is None:
            self._console.print(f"[red]task not found:[/red] {task_id}")
            return
        self._console.print(
            Panel(render_text(snapshot), title=f"todo {task_id}", border_style="cyan")
        )


def _ts_iter(events: Iterable) -> list[datetime]:  # pragma: no cover - reserved
    return [e.timestamp for e in events]


__all__: tuple[str, ...] = ("ChatREPL", "ChatTranscript")
