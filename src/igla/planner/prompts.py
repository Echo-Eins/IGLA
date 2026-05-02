"""Prompt construction for the planner.

LM Studio (and any OpenAI-compatible local server backed by llama.cpp / mlc /
vLLM) reuses its KV cache when the leading tokens of a request are identical
to those of a previous request. To exploit that, prompt assembly is split into
three stability layers:

* :func:`build_session_system_message` — *session-stable*. Role, action grammar,
  hard rules, and the tool catalog. Identical for every turn in a chat session
  (tools change only when ``ToolRegistry`` is mutated).
* :func:`build_task_system_message` — *task-stable*. The current task: id,
  goal, raw request, constraints, success criteria. Identical for every turn
  inside a single task.
* :func:`build_turn_user_message` — *turn-fresh*. Only what changed since the
  last LLM call: runtime mode, allowed/forbidden actions, last rejection,
  newly recorded events, current TODO summary, iteration counter.

The convenience function :func:`build_proposal_messages` composes all three
in order. New layers (e.g. a long-term-memory recap) slot in by adding a
new builder + extending :func:`build_proposal_messages` — no caller-side
breakage.

The model never sees the JSON Schema directly; the schema is shipped via
``response_format=json_schema`` (see ``llm_client.py``). The OpenAI-compatible
server compiles it once into a logits-processor and reuses it across calls.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from ..protocol.policy import PolicyDecision
from ..protocol.proposal import PlannerProposal
from ..protocol.runtime import RuntimeStateSnapshot
from ..protocol.task import TaskSpec
from ..protocol.todo import TodoTreeSnapshot
from ..todo.render import render_text
from .llm_client import LLMChatMessage

# ---------------------------------------------------------------------------
# Session-level (stable for the whole chat session)
# ---------------------------------------------------------------------------

SESSION_ROLE = """\
Ты — Planner системы IGLA.

ИГЛА — локальный проверяющий runtime. Модель никогда не выполняет действий
сама. Ты только предлагаешь следующий шаг как один JSON-объект
PlannerProposal. Runtime (Kernel + PolicyEngine + StateMachine + ToolRegistry
+ Executor) решает, разрешён ли шаг, выполняет его и возвращает результат на
следующем ходу.

Главное правило: действия, не ответы. ИГЛА не отвечает на вопросы — она
выполняет шаги, наблюдает результат и продолжает.\
"""

# Принцип автономии — выносим в отдельную секцию ВЕРХНЕГО уровня промпта,
# чтобы модель не пропустила его, скользя по списку правил.
SESSION_AUTONOMY = """\
ПОЛНАЯ АВТОНОМИЯ — НЕ СПРАШИВАЙ, ИЩИ САМ.

ИГЛА самостоятельно собирает факты через локальные tools. Запрос к
пользователю — это исключение, разрешённое только когда:
  * read-only discovery tools (list_dir / find_files / search_text / read_file)
    реально были вызваны в этой задаче и вернули недостаточный результат, ИЛИ
  * runtime в режиме FAILURE_DIAGNOSIS_REQUIRED после фактической ошибки tool.

На первом ходу `ask_user_clarification` ЗАПРЕЩЁН policy-движком и будет
отклонён с reason_code=MUST_DISCOVER_FIRST. Сначала list_dir/find_files/search_text,
потом, если действительно тупик — вопрос. Не спрашивай у пользователя то,
что можно увидеть глазами в его рабочей директории.\
"""

SESSION_HARD_RULES: tuple[str, ...] = (
    "Возвращай ровно ОДИН JSON-объект PlannerProposal и ничего больше. "
    "Никакого текста вокруг, никакого Markdown, никаких комментариев.",
    "Поле `action` обязательно. Допустимые значения: tool_invocation, "
    "ask_user_clarification, todo_branch, todo_complete, declare_task_done.",
    "Выбирай только действия из allowed_next_actions текущего хода. "
    "Никогда не выбирай из forbidden_next_actions.",
    "Используй только tool из available_tools (см. секцию TOOLS ниже). "
    "tool_name и tool_version должны совпадать с available_tools дословно — "
    "не придумывай имена и не изменяй версию.",
    "Не выдумывай состояние файлов, путей, команд, строк, логов или "
    "результатов tool. Если не знаешь — узнавай через tool, не через догадку.",
    "Прежде чем спрашивать пользователя про файлы или пути, обязательно "
    "попробуй list_dir, find_files и search_text. ask_user_clarification — "
    "последнее средство, не первое.",
    "Как только результат tool даёт достаточно данных чтобы ответить на цель "
    "задачи — СРАЗУ вызывай declare_task_done с ответом в поле summary. "
    "Не вызывай больше tools если ответ уже есть в событиях.",
    "После ошибки tool ты в режиме FAILURE_DIAGNOSIS_REQUIRED. Запрещено "
    "вслепую повторять тот же класс действий. Сначала диагностика "
    "(list_dir / find_files / search_text / read_file), потом другая попытка.",
    "Для задачи 'создай копию файла / скопируй файл в новый путь / допиши текст "
    "к копии' используй copy_file. НЕ собирай большой файл в patch_file.new_content "
    "и НЕ используй patch_file для создания несуществующего файла.",
    "patch_file требует обязательной последовательности: сначала read_file "
    "того же пути, затем patch_file с base_sha256=<file_sha256 из read_file>. "
    "Без свежего read_file политика отклонит patch (MUST_READ_BEFORE_WRITE / "
    "HASH_MISMATCH_FILE_CHANGED). После каждого успешного patch_file файл "
    "нужно перечитать перед следующим патчем — file_sha256 уже изменился.",
    "patch_file — это либо new_content (полная замена), либо search+replacement "
    "(точечная). Не используй оба сразу. Если search встречается несколько раз — "
    "добавь больше контекста или поставь replace_all=true.",
    "Если предыдущий ход был отклонён (last_rejection), внимательно прочитай "
    "reason_code, message и hints — они содержат точный список доступных "
    "инструментов. Не повторяй тот же tool_name или tool_version.",
)

SESSION_OUTPUT_GRAMMAR = """\
Каждый ответ — это один JSON-объект следующей формы (один из вариантов
по полю `action`):

  {"action":"tool_invocation","reason":"...","tool_name":"<name>",
   "tool_version":"<x.y.z>","input":{...},"expected_outputs":[],
   "target_todo_node_id":null}

  {"action":"ask_user_clarification","reason":"...","question":"...",
   "creates_clarification_node":true,"target_todo_node_id":null}

  {"action":"todo_branch","reason":"...","parent_node_id":"<id>",
   "children":[{"title":"...","kind":"subgoal","description":null,
                "clarification_question":null}]}

  {"action":"todo_complete","reason":"...","node_id":"<id>","summary":"..."}

  {"action":"declare_task_done","reason":"...","summary":"..."}\
"""

# ----- Tool usage examples (2-3 per tool, kept inline so prompt is self-
# contained for the model). Examples are illustrative, not exhaustive — the
# canonical contract is `input_schema`.
TOOL_USAGE_EXAMPLES: dict[str, list[dict[str, Any]]] = {
    "list_dir": [
        {
            "intent": "Обзор структуры всего workspace перед началом задачи.",
            "input": {"depth": 2},
        },
        {
            "intent": "Изучить содержимое конкретной директории на один уровень.",
            "input": {"path": "src/igla", "depth": 1},
        },
        {
            "intent": (
                "Развернуть поддиректорию глубже после первичного обзора. "
                "Если depth > 4, будет DEPTH_LIMIT_EXCEEDED — используй меньший depth "
                "и вызывай list_dir на каждой поддиректории отдельно."
            ),
            "input": {"path": "src/igla/tools", "depth": 3},
        },
    ],
    "find_files": [
        {
            "intent": "Найти файл по имени, когда пользователь упомянул только название.",
            "input": {"query": "00-review.md", "max_results": 20},
        },
        {
            "intent": "Найти все Python-модули в src.",
            "input": {"glob": "src/**/*.py", "max_results": 100},
        },
        {
            "intent": "Скрытые конфиги в корне проекта.",
            "input": {"glob": ".*", "include_hidden": True, "max_results": 50},
        },
    ],
    "search_text": [
        {
            "intent": "Найти, где определён символ `build_runtime` в тестах.",
            "input": {"query": "def build_runtime", "path_glob": "tests/**/*.py"},
        },
        {
            "intent": "Все TODO-метки в Markdown-документации.",
            "input": {"query": "TODO", "path_glob": "docs/*.md"},
        },
        {
            "intent": "Поиск точной строки сообщения об ошибке во всём workspace.",
            "input": {"query": "MAX_CONSECUTIVE_REJECTIONS", "max_results": 25},
        },
    ],
    "read_file": [
        {
            "intent": (
                "Прочитать небольшой файл целиком — путь уже известен из find_files."
            ),
            "input": {"path": "configs/motivation.yaml"},
        },
        {
            "intent": (
                "Большой файл: первые 1000 строк. Если file имеет больше 1000 строк, "
                "ОБЯЗАТЕЛЬНО указывай start_line и end_line иначе будет ошибка FILE_TOO_LARGE."
            ),
            "input": {"path": "src/igla/planner/planner.py", "start_line": 0, "end_line": 1000},
        },
        {
            "intent": (
                "Итеративное чтение: следующий блок после первого (строки 1000–2000). "
                "Продолжать пока end_of_file=false."
            ),
            "input": {"path": "src/igla/planner/planner.py", "start_line": 1000, "end_line": 2000},
        },
    ],
    "copy_file": [
        {
            "intent": (
                "Создать копию файла в новом пути. Используй это вместо patch_file, "
                "когда destination ещё не существует."
            ),
            "input": {
                "source_path": "README.md",
                "destination_path": "README1.md",
            },
        },
        {
            "intent": (
                "Скопировать файл и дописать текст в конец копии, не протаскивая "
                "весь исходный файл через LLM context."
            ),
            "input": {
                "source_path": "README.md",
                "destination_path": "README1.md",
                "append_text": "\nhello\n",
            },
        },
        {
            "intent": (
                "Заменить существующую копию только если это явно нужно. "
                "overwrite=true создаёт backup перед заменой."
            ),
            "input": {
                "source_path": "README.md",
                "destination_path": "README1.md",
                "overwrite": True,
            },
        },
    ],
    "patch_file": [
        {
            "intent": (
                "Точечная замена: заменить ровно одну строку. base_sha256 — это "
                "поле file_sha256 из последнего read_file этого файла. Если в файле "
                "несколько вхождений search, добавь больше контекста в search или "
                "поставь replace_all=true."
            ),
            "input": {
                "path": "src/igla/main.py",
                "base_sha256": "sha256:0123abcd... (из read_file output.file_sha256)",
                "search": "DEBUG = False",
                "replacement": "DEBUG = True",
            },
        },
        {
            "intent": (
                "Полная перезапись короткого файла: используй new_content. "
                "Файл всё равно нужно сначала прочитать read_file и передать base_sha256."
            ),
            "input": {
                "path": "configs/feature.toml",
                "base_sha256": "sha256:abcd1234...",
                "new_content": "[feature]\nenabled = true\n",
            },
        },
        {
            "intent": (
                "Заменить все вхождения старого имени на новое (refactor). "
                "Только когда search достаточно специфичный, чтобы не задеть лишнее."
            ),
            "input": {
                "path": "src/igla/foo.py",
                "base_sha256": "sha256:beef...",
                "search": "old_name",
                "replacement": "new_name",
                "replace_all": True,
            },
        },
    ],
    "restore_file": [
        {
            "intent": (
                "Откатить последний patch_file этого файла. backup_artifact_id — это "
                "значение output.backup_artifact_id из соответствующего patch_file."
            ),
            "input": {
                "backup_artifact_id": "art_01HX...",
                "reason": "verifier failed после применения патча",
            },
        },
    ],
"verify_file": [
        {
            "intent": (
                "Проверить файл, который ты ТОЛЬКО ЧТО патчил/читал. Без аргумента "
                "checks инструмент сам выберет проверки по расширению: .py → "
                "syntax+lint, .json → syntax, .yaml/.yml → syntax. pytest никогда "
                "не запускается автоматически — только если явно указать checks."
            ),
            "input": {
                "path": "src/igla/foo.py",
                "reason": "после patch_file проверяем синтаксис и lint",
            },
        },
        {
            "intent": (
                "Запустить pytest на конкретном тестовом файле. test_path обязателен "
                "при checks=['pytest'], должен лежать внутри workspace."
            ),
            "input": {
                "path": "src/igla/foo.py",
                "checks": ["pytest"],
                "test_path": "tests/test_foo.py",
                "reason": "проверить что patch не сломал тесты",
            },
        },
        {
            "intent": (
                "Только синтаксис (например, для большого файла когда lint избыточен). "
                "verify_file требует наличия read_file/patch_file receipt для path."
            ),
            "input": {
                "path": "configs/motivation.yaml",
                "checks": ["syntax"],
            },
        },
    ],
    "read_task_log": [
        {
            "intent": (
                "Получить компактную историю своей работы в текущей задаче: "
                "вызовы tool, статусы, отказы политики, файлы которые ты трогал. "
                "Полезно при длинной задаче, когда хвост событий уже не помещается "
                "в текущий промпт."
            ),
            "input": {"reason": "проверяю что уже сделано"},
        },
        {
            "intent": (
                "Сократить до последних 20 шагов когда задача длинная. Свежие записи "
                "сохраняются — старые отбрасываются."
            ),
            "input": {"max_entries": 20},
        },
    ],
    "ask_user": [
        {
            "intent": (
                "Использовать ТОЛЬКО когда find_files и search_text вернули пусто "
                "И задача без этих данных невыполнима."
            ),
            "input": {
                "question": (
                    "Не нашёл ни одного файла, подходящего под описание. "
                    "Уточни путь или проектную папку."
                )
            },
        },
    ],
    "noop_observe": [
        {
            "intent": (
                "Зафиксировать наблюдение/контрольную точку без побочных эффектов "
                "(например, после диагностики неудачи)."
            ),
            "input": {"note": "find_files вернул 0 результатов для query=foo"},
        },
    ],
}


@dataclass(frozen=True)
class ToolDigest:
    """Compact, prompt-friendly view of a registered tool."""

    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    risk_level: str
    side_effects: bool


def tools_digest_from_registry_dump(tools: list[dict[str, Any]]) -> list[ToolDigest]:
    """Convert the dicts produced by ``registry.list_tools`` into digests.

    Tolerant to missing keys so test stubs can pass partial data.
    """
    digests: list[ToolDigest] = []
    for t in tools:
        digests.append(
            ToolDigest(
                name=str(t.get("name", "")),
                version=str(t.get("version", "")),
                description=str(t.get("description", "")),
                input_schema=dict(t.get("input_schema") or {}),
                risk_level=str(t.get("risk_level", "")),
                side_effects=bool(t.get("side_effects", False)),
            )
        )
    return digests


def _format_tools_block(tools: list[ToolDigest]) -> str:
    if not tools:
        return "(no tools registered)"
    lines: list[str] = []
    for tool in tools:
        examples = TOOL_USAGE_EXAMPLES.get(tool.name, [])
        header = (
            f"- {tool.name} (v{tool.version}) "
            f"[risk={tool.risk_level}, side_effects={'yes' if tool.side_effects else 'no'}]"
        )
        lines.append(header)
        if tool.description:
            lines.append(f"    description: {tool.description}")
        if tool.input_schema:
            lines.append(
                "    input_schema: "
                + json.dumps(tool.input_schema, ensure_ascii=False, separators=(",", ":"))
            )
        if examples:
            lines.append("    examples:")
            for ex in examples:
                lines.append(
                    "      - "
                    + json.dumps(
                        {"intent": ex["intent"], "input": ex["input"]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
    return "\n".join(lines)


def _format_rules_block(rules: Iterable[str]) -> str:
    return "\n".join(f"  {i+1}. {rule}" for i, rule in enumerate(rules))


def build_session_system_message(
    tools: list[ToolDigest] | list[dict[str, Any]],
    *,
    extra_sections: list[tuple[str, str]] | None = None,
) -> LLMChatMessage:
    """Session-stable system message: role, rules, output grammar, tools.

    ``extra_sections`` is a deliberately simple extension point — pass a list
    of ``(title, body)`` tuples to inject additional sections (e.g. project
    conventions, future memory recap) without modifying this function.
    """
    if tools and isinstance(tools[0], ToolDigest):
        digests = cast(list[ToolDigest], tools)
    else:
        digests = tools_digest_from_registry_dump(cast(list[dict[str, Any]], tools))
    blocks: list[str] = [
        "ROLE",
        SESSION_ROLE,
        "",
        "AUTONOMY",
        SESSION_AUTONOMY,
        "",
        "HARD RULES",
        _format_rules_block(SESSION_HARD_RULES),
        "",
        "OUTPUT GRAMMAR",
        SESSION_OUTPUT_GRAMMAR,
        "",
        "TOOLS",
        _format_tools_block(digests),
    ]
    for title, body in extra_sections or []:
        blocks.extend(["", title.upper(), body])
    return LLMChatMessage(role="system", content="\n".join(blocks))


# ---------------------------------------------------------------------------
# Task-level (stable for the whole task lifetime)
# ---------------------------------------------------------------------------


def build_task_system_message(task: TaskSpec) -> LLMChatMessage:
    """Task-stable system message — task identity & invariant goal/constraints."""
    body = {
        "task_id": task.task_id,
        "raw_request": task.raw_request,
        "goal": task.goal,
        "constraints": json.loads(task.constraints.model_dump_json()),
        "success_criteria": list(task.success_criteria),
    }
    rendered = (
        "TASK\n"
        + json.dumps(body, ensure_ascii=False, indent=2)
    )
    return LLMChatMessage(role="system", content=rendered)


# ---------------------------------------------------------------------------
# Turn-level (per-iteration delta)
# ---------------------------------------------------------------------------


def build_turn_user_message(
    *,
    runtime: RuntimeStateSnapshot,
    todo: TodoTreeSnapshot,
    last_decision: PolicyDecision | None,
    new_events: list[dict[str, Any]] | None,
    chat_tail: list[dict[str, str]] | None = None,
    extras: dict[str, Any] | None = None,
) -> LLMChatMessage:
    """Per-turn delta. Keep it small — large fields belong in the system
    layer or in EventStore (which the model can sample via tools).
    """
    pack: dict[str, Any] = {
        "iteration": runtime.iteration,
        "mode": runtime.mode.value,
        "allowed_next_actions": list(runtime.allowed_next_actions),
        "forbidden_next_actions": list(runtime.forbidden_next_actions),
        "consecutive_rejections": runtime.consecutive_rejections,
        "failure_classified": runtime.failure_classified,
        "changed_condition_declared": runtime.changed_condition_declared,
        "last_error_code": runtime.last_error_code,
        "todo_summary": render_text(todo, with_ids=True),
    }
    if new_events:
        pack["new_events_since_last_turn"] = list(new_events)
    if chat_tail:
        pack["chat_tail"] = list(chat_tail)
    rejection = (
        None
        if last_decision is None or last_decision.is_allow
        else last_decision.rejection
    )
    if rejection is not None and last_decision is not None:
        rej_block: dict[str, Any] = {
            "reason_code": rejection.reason_code,
            "message": rejection.message,
            "rule_id": rejection.rule_id,
            "allowed_next_actions": list(last_decision.allowed_next_actions),
            "forbidden_next_actions": list(last_decision.forbidden_next_actions),
        }
        if rejection.hints:
            rej_block["hints"] = list(rejection.hints)
        pack["last_rejection"] = rej_block
    if extras:
        # Reserved for future expansion (e.g. memory snippets). Caller owns
        # the keys; we put them under a namespaced bucket so they never
        # collide with the canonical fields above.
        pack["extras"] = dict(extras)
    return LLMChatMessage(
        role="user",
        content=json.dumps(pack, ensure_ascii=False, separators=(",", ":")),
    )


# ---------------------------------------------------------------------------
# Top-level composer
# ---------------------------------------------------------------------------


def build_proposal_messages(
    *,
    task: TaskSpec,
    runtime: RuntimeStateSnapshot,
    todo: TodoTreeSnapshot,
    last_decision: PolicyDecision | None,
    available_tools: list[dict[str, Any]] | list[ToolDigest],
    new_events: list[dict[str, Any]] | None = None,
    chat_tail: list[dict[str, str]] | None = None,
    extras: dict[str, Any] | None = None,
    extra_session_sections: list[tuple[str, str]] | None = None,
    cached_session_message: LLMChatMessage | None = None,
    cached_task_message: LLMChatMessage | None = None,
    # Backwards-compat alias for callers/tests that still use the old name.
    last_event_log_tail: list[dict[str, Any]] | None = None,
) -> list[LLMChatMessage]:
    """Compose the final ``[system, system, user]`` triple.

    The two system messages are deterministic functions of (tools, task) and
    can be cached by the caller for KV-cache reuse on the LM Studio side.
    Pass ``cached_session_message`` / ``cached_task_message`` to skip
    rebuilding them.
    """
    if new_events is None and last_event_log_tail is not None:
        new_events = last_event_log_tail
    session_msg = cached_session_message or build_session_system_message(
        available_tools, extra_sections=extra_session_sections
    )
    task_msg = cached_task_message or build_task_system_message(task)
    turn_msg = build_turn_user_message(
        runtime=runtime,
        todo=todo,
        last_decision=last_decision,
        new_events=new_events,
        chat_tail=chat_tail,
        extras=extras,
    )
    return [session_msg, task_msg, turn_msg]


# ---------------------------------------------------------------------------
# Schema for ``response_format`` (unchanged)
# ---------------------------------------------------------------------------


def build_proposal_schema() -> dict[str, Any]:
    """JSON Schema for ``PlannerProposal`` suitable for ``response_format``."""
    schema = PlannerProposal.model_json_schema(by_alias=False)
    _normalise_schema(schema)
    return schema


def _normalise_schema(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        properties = node.get("properties")
        if isinstance(properties, dict) and "action" in properties:
            required = list(node.get("required") or [])
            if "action" not in required:
                required.append("action")
                node["required"] = required
            if isinstance(properties["action"], dict):
                properties["action"].pop("default", None)
        for value in node.values():
            _normalise_schema(value)
    elif isinstance(node, list):
        for item in node:
            _normalise_schema(item)


# ---------------------------------------------------------------------------
# Event tail rendering (now used for *delta* slices in the planner)
# ---------------------------------------------------------------------------


def render_event_tail(
    events: Iterable[Any], *, limit: int = 12, since_index: int | None = None
) -> list[dict[str, Any]]:
    """Render events for inclusion in the prompt.

    If ``since_index`` is given, render every event from that index onward
    (the planner uses this to send only the *delta* since the last LLM call).
    Otherwise we keep the legacy "last N" behaviour.
    """
    items = list(events)
    if since_index is not None:
        items = items[since_index:]
    elif limit > 0:
        items = items[-limit:]
    tail: list[dict[str, Any]] = []
    for evt in items:
        digest: dict[str, Any] = {
            "kind": evt.kind.value,
            "actor": evt.actor,
            "step_id": evt.step_id,
            "payload_keys": sorted(evt.payload.keys()),
        }
        for field in ("status", "tool_name", "message", "rule", "reason_code", "answer"):
            if field in evt.payload and not isinstance(evt.payload[field], (dict, list)):
                digest[field] = evt.payload[field]
        if "output" in evt.payload and isinstance(evt.payload["output"], dict):
            digest["output"] = evt.payload["output"]
        tail.append(digest)
    return tail


__all__ = (
    "SESSION_ROLE",
    "SESSION_AUTONOMY",
    "SESSION_HARD_RULES",
    "SESSION_OUTPUT_GRAMMAR",
    "TOOL_USAGE_EXAMPLES",
    "ToolDigest",
    "tools_digest_from_registry_dump",
    "build_session_system_message",
    "build_task_system_message",
    "build_turn_user_message",
    "build_proposal_messages",
    "build_proposal_schema",
    "render_event_tail",
)
