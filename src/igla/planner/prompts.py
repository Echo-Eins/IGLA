"""Prompt construction for the planner.

We send the model a *narrow* contract:

1. A short system prompt describing IGLA's role rules (verbatim, never
   rendered from runtime state — it is the model's "constitution").
2. A user message containing a JSON pack of context: task, runtime state,
   TODO tree, last rejection, allowed/forbidden actions.
3. The structured-output schema for ``PlannerProposal``.

The system prompt deliberately repeats the kernel's enforcement rules.
The model "knowing" them is a nicety: the kernel still enforces them.
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

SYSTEM_PROMPT = """\
Ты — Planner системы IGLA. ИГЛА не отвечает на вопросы и не пишет тексты,
она выполняет действия. Твоя задача — на каждом шаге вернуть **строго
один** JSON-объект — ``PlannerProposal``.

Жёсткие правила (всё, что им не соответствует, runtime отклонит):

1. Поле ``action`` — одно из: ``tool_invocation``, ``ask_user_clarification``,
   ``todo_branch``, ``todo_complete``, ``declare_task_done``.
2. Если runtime прислал ``allowed_next_actions``, выбирай только из этого
   списка. Если runtime прислал ``forbidden_next_actions`` — никогда не
   используй такие.
3. Никогда не возвращай свободный текст, рассуждения или дополнительный
   JSON вне корневого объекта.
4. ``reason`` — короткое (1–2 предложения) обоснование, **зачем** это
   действие именно сейчас. Без него — отказ.
5. ``target_todo_node_id`` указывай, если действие относится к конкретному
   узлу TODO (используй ``id`` из дерева).
6. После провала шага runtime включает FAILURE_DIAGNOSIS_REQUIRED. В этом
   режиме запрещены ``tool_invocation`` и ``declare_task_done``. Сначала
   разберись (clarification / todo_branch / todo_complete).
7. Не повторяй уже отвергнутый proposal. ``last_rejection`` всегда виден.
8. Не выдумывай tool_name: используй только тех, что в ``available_tools``.
9. ``declare_task_done`` допустим только когда все open-узлы TODO закрыты
   или явно abandoned, и цель достигнута.
10. Не спрашивай пользователя, где находится файл/строка/символ, пока не
    использованы доступные локальные discovery tools. Для запроса вида
    «открой/прочитай файл X» сначала используй ``find_files`` с именем X,
    затем ``read_file`` по найденному пути. ``ask_user_clarification`` для
    пути допустим только если поиск дал 0 результатов, несколько одинаково
    подходящих кандидатов, конфликт с TODO/policy или капитальную поломку.

Никогда не пиши приветствий, объяснений, кода. Только один JSON-объект.
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
        "last_rejection": (
            None
            if last_decision is None or last_decision.is_allow
            else {
                "reason_code": last_decision.rejection.reason_code if last_decision.rejection else None,
                "message": last_decision.rejection.message if last_decision.rejection else None,
                "rule_id": last_decision.rejection.rule_id if last_decision.rejection else None,
                "allowed_next_actions": list(last_decision.allowed_next_actions),
                "forbidden_next_actions": list(last_decision.forbidden_next_actions),
            }
        ),
        "recent_events": list(last_event_log_tail or []),
        "chat_tail": list(chat_tail or []),
    }

    user = (
        "Контекст задачи и состояния runtime ниже. Верни ровно один "
        "JSON-объект формата PlannerProposal.\n\n"
        f"{json.dumps(pack, ensure_ascii=False, indent=2)}"
    )
    return [
        LLMChatMessage(role="system", content=SYSTEM_PROMPT),
        LLMChatMessage(role="user", content=user),
    ]


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
