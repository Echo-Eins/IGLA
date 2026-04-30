"""Prompt construction for the planner.

The repeated LM Studio payload must stay small. Kernel policy, response schema,
and Pydantic validation enforce the contract; no per-iteration system message
is sent in the MVP hot path.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ..protocol.policy import PolicyDecision
from ..protocol.proposal import PlannerProposal
from ..protocol.runtime import RuntimeStateSnapshot
from ..protocol.task import TaskSpec
from ..protocol.todo import TodoTreeSnapshot
from ..todo.render import render_dict_for_prompt, render_text
from .llm_client import LLMChatMessage

PLANNER_BOOTSTRAP_PROMPT = """\
Ты — Planner системы IGLA.

ИГЛА — локальный проверяющий runtime, где модель не исполняет действия
напрямую. Модель только предлагает структурированный следующий шаг, а Runtime
через Kernel, PolicyEngine, StateMachine, ToolRegistry и Executor решает,
можно ли этот шаг выполнить.

Главные правила:

1. Возвращай только один JSON-объект PlannerProposal.
2. Не пиши свободный текст, рассуждения, приветствия или Markdown вне JSON.
3. Выбирай только действия из allowed_next_actions.
4. Никогда не выбирай действия из forbidden_next_actions.
5. Используй только tools из available_tools.
6. Не выдумывай состояние файлов, команд, логов, системы или памяти.
7. Для файлов, путей, строк и символов сначала используй локальные tools:
   find_files, search_text, read_file.
8. ask_user_clarification допустим только при неоднозначности, конфликте
   TODO/policy, отсутствии результатов локального поиска или hard failure.
9. После failure нельзя повторять тот же класс действий вслепую. Сначала
   диагностика, новый evidence или изменённое условие.
10. declare_task_done допустим только когда цель достигнута и открытые TODO
    закрыты или явно abandoned.

Runtime всё равно проверит JSON, schema, allowed actions, tools и policy.
Если ты ошибёшься, Kernel отклонит proposal и вернёт last_rejection.
"""


def build_proposal_messages(
    *,
    task: TaskSpec,
    runtime: RuntimeStateSnapshot,
    todo: TodoTreeSnapshot,
    last_decision: PolicyDecision | None,
    last_event_log_tail: list[dict[str, Any]] | None,
    available_tools: list[dict[str, Any]],
    chat_tail: list[dict[str, str]] | None = None,
) -> list[LLMChatMessage]:
    pack: dict[str, Any] = {
        "request": {
            "kind": "planner_step",
            "output": "PlannerProposal",
            "rules": [
                "choose_only_allowed_next_actions",
                "never_choose_forbidden_next_actions",
                "use_available_tools_only",
                "local_discovery_before_user_clarification",
                "return_json_only",
            ],
        },
        "task": {
            "task_id": task.task_id,
            "raw_request": task.raw_request,
            "goal": task.goal,
            "status": task.status.value,
            "constraints": json.loads(task.constraints.model_dump_json()),
            "success_criteria": list(task.success_criteria),
        },
        "runtime": {
            "mode": runtime.mode.value,
            "iteration": runtime.iteration,
            "consecutive_rejections": runtime.consecutive_rejections,
            "allowed_next_actions": list(runtime.allowed_next_actions),
            "forbidden_next_actions": list(runtime.forbidden_next_actions),
            "failure_classified": runtime.failure_classified,
            "changed_condition_declared": runtime.changed_condition_declared,
            "last_error_code": runtime.last_error_code,
        },
        "todo_tree_summary": render_text(todo, with_ids=True),
        "todo_tree": render_dict_for_prompt(todo),
        "available_tools": available_tools,
        "last_rejection": None,
        "recent_events": list(last_event_log_tail or []),
        "chat_tail": list(chat_tail or []),
    }
    rejection = None if last_decision is None or last_decision.is_allow else last_decision.rejection
    if rejection is not None:
        pack["last_rejection"] = {
            "reason_code": rejection.reason_code,
            "message": rejection.message,
            "rule_id": rejection.rule_id,
            "allowed_next_actions": list(last_decision.allowed_next_actions),
            "forbidden_next_actions": list(last_decision.forbidden_next_actions),
        }

    user = json.dumps(
        pack,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [LLMChatMessage(role="user", content=user)]


def build_proposal_schema() -> dict[str, Any]:
    """JSON Schema for ``PlannerProposal`` suitable for ``response_format``.

    We derive the schema from Pydantic, then post-process it so that LM Studio
    accepts it (it is picky about top-level ``oneOf`` discriminators).
    """
    schema = PlannerProposal.model_json_schema(by_alias=False)
    # Pydantic v2 emits ``oneOf`` for the discriminated union at the root.
    # LM Studio + most local backends accept it directly. We normalise the
    # title / set ``additionalProperties=false`` recursively where missing.
    _normalise_schema(schema)
    return schema


def _normalise_schema(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        for value in node.values():
            _normalise_schema(value)
    elif isinstance(node, list):
        for item in node:
            _normalise_schema(item)


def render_event_tail(events: Iterable, *, limit: int = 12) -> list[dict[str, Any]]:
    """Render the last N events for inclusion in the prompt.

    Kept tiny: only ``kind``, ``actor``, and a short payload digest. Full
    payloads stay in EventStore — the planner asks for details via tools.
    """
    tail: list[dict[str, Any]] = []
    for evt in events:
        digest = {
            "kind": evt.kind.value,
            "actor": evt.actor,
            "step_id": evt.step_id,
            "payload_keys": sorted(evt.payload.keys()),
        }
        # Promote a few well-known scalar fields for visibility.
        for field in ("status", "tool_name", "message", "rule", "reason_code", "answer"):
            if field in evt.payload and not isinstance(evt.payload[field], (dict, list)):
                digest[field] = evt.payload[field]
        tail.append(digest)
    return tail[-limit:]
