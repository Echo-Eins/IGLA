# Engine — реализованный слой ядра

Этот документ — карта **реально написанного** кода в `src/igla/` и его соответствия архитектурной документации (00–12).

## Код по слоям

```
src/igla/
├── ids.py                ULID-генератор
├── config.py             Pydantic-модели IglaSettings, LMStudioSettings, PathsSettings
├── state_reset.py        Safe reset for workspace `.igla` runtime state
├── protocol/             Замороженные envelope'ы (см. 04-tool-protocol.md)
│   ├── invocation.py     ToolInvocation + Context/PolicyHints/Dependencies/Provenance
│   ├── result.py         ToolResult, ToolError, ToolResultMetric
│   ├── manifest.py       ToolManifest + ArtifactSpec/PolicyRequirements/SandboxSpec/...
│   ├── artifact.py       ArtifactDescriptor, ArtifactCreateRequest, ArtifactRef
│   ├── event.py          EventRecord + EventKind (полный набор для MVP)
│   ├── evidence.py       EvidenceRecord, EvidenceCreateRequest, EvidenceRef
│   ├── policy.py         ActionRequest, PolicyDecision, PolicyRejection
│   ├── receipt.py        FileReadReceipt
│   ├── plan.py           Plan, PlanStep, StepStatus, VerificationSpec, OnFailureSpec
│   ├── task.py           TaskSpec, TaskStatus, TaskConstraints
│   ├── todo.py           TodoNode, TodoStatus, TodoNodeKind, TodoTreeSnapshot
│   ├── runtime.py        RuntimeMode, RuntimeStateSnapshot
│   └── proposal.py       PlannerProposal (discriminated union)
├── kernel/               Детерминированное ядро (см. 03-kernel-and-policies.md)
│   ├── kernel.py         Kernel facade
│   ├── clock.py          Clock protocol + System/Fixed/StepClock
│   ├── errors.py         KernelError, ToolNotFoundError, ToolValidationError, ...
│   ├── event_store.py    JSONL append-only EventStore
│   ├── artifact_store.py Immutable directory-per-artifact ArtifactStore
│   ├── evidence_store.py JSONL EvidenceStore
│   ├── receipt_manager.py FileReadReceipt + intake markers
│   ├── schema_validator.py Draft 2020-12 JSON Schema validator
│   ├── registry.py       ToolRegistry: register/get/resolve/list_tools
│   ├── state_machine.py  StepStatus и RuntimeMode автоматы + TaskRuntimeState
│   └── executor.py       Запуск Tool.invoke с timeout-ом и схема-валидацией
├── policies/             Policy Engine + конституция
│   ├── predicates.py     8 встроенных предикатов
│   ├── constitution.py   Загрузка constitution.yaml
│   └── engine.py         PolicyEngine (первый DENY побеждает)
├── motivation/           Декларативные циклы
│   ├── rule.py           MotivationRule + load_rules
│   ├── conditions.py     10 встроенных условий
│   ├── effects.py        12 встроенных эффектов (set_mode, pause/resume, ...)
│   └── cycle.py          MotivationCycle.dispatch(event)
├── todo/                 Дерево TODO с ветвлением
│   ├── tree.py           TodoTree + агрегация статусов
│   ├── store.py          Атомарная JSON-персистенция per-task
│   └── render.py         Текстовый и dict рендер
├── planner/              Планировщик + LM Studio клиент
│   ├── llm_client.py     LMStudioClient + OfflineCannedClient
│   ├── prompts.py        PLANNER_BOOTSTRAP_PROMPT + compact JSON prompt builder
│   └── planner.py        Главный цикл run_task / handle_user_input
├── tools/                Tool API + builtins
│   ├── base.py           Tool ABC
│   └── builtin/
│       ├── ask_user.py
│       ├── search.py     find_files + search_text workspace discovery
│       ├── read_file.py
│       └── noop_observe.py
├── chat/
│   ├── plain.py          PlainCLI: copyable text output + event timeline
│   └── repl.py           ChatREPL + ConsoleAskUserChannel + Rich-вывод
└── cli.py                igla {chat,run,reset-state,rich-chat,doctor,version}
```

## Конфиги

| Файл | О чём |
|------|-------|
| `configs/constitution.yaml` | Список активных предикатов конституции |
| `configs/motivation.yaml`   | Декларативные правила (триггеры/условия/эффекты) |

Конституция — **жёсткая**: выключение предиката не поддерживается в production.
Мотивация — **легко правится**: добавить новое поведение = добавить условие/эффект в Python-реестр + правило в YAML.

## Ключевые инварианты ядра (реализовано)

* **LLM proposes. Runtime disposes.** Планировщик отдаёт только `PlannerProposal`; всё остальное — kernel.
* **Schema-validated.** Каждый ToolInvocation/ToolResult валидируется через Pydantic + JSON Schema.
* **No blind retry.** Предикат `no_blind_retry` блокирует мутирующие действия в `FAILURE_DIAGNOSIS_REQUIRED`, но допускает явно разрешённые read-only diagnostic tools.
* **Pause/resume через clarification.** `pause_for_clarification` / `resume_from_clarification` сохраняют пред-паузный режим (включая `FAILURE_DIAGNOSIS_REQUIRED`).
* **Bounded loops.** Хард-лимит итераций и хард-лимит подряд идущих rejection'ов в Policy Engine.
* **Append-only audit.** EventStore — JSONL, толерантный к torn lines.
* **Idempotent task creation.** `TASK_CREATED` эмитится **один раз** на task (важно для resume).

## CLI

```bash
# Запуск plain интерактивного чата (по умолчанию):
igla --workspace /path/to/work chat

# Один запрос с copyable output:
igla --workspace /path/to/work run "найди файл AGENTS.md и открой его"

# Reset poisoned runtime state (.igla):
igla --workspace /path/to/work reset-state

# Старый Rich REPL:
igla --workspace /path/to/work rich-chat

# Сводка по конфигам, политикам, мотивации, тулам:
igla --workspace /path doctor

# Версия:
igla version
```

Переменные окружения / флаги для LM Studio:

```
IGLA_WORKSPACE          (или --workspace)
IGLA_LMSTUDIO_URL       (или --lm-url)
IGLA_LMSTUDIO_MODEL     (или --lm-model)
IGLA_LMSTUDIO_KEY       (или --lm-key, default: 'lm-studio')
                        --no-schema  использовать json_object fallback
```

## Тесты

`tests/` — 85 кейсов:

| Файл | Покрывает |
|------|-----------|
| `test_protocol.py`       | замороженность envelope'ов, дискриминатор PlannerProposal |
| `test_discovery_tools.py` | workspace-bounded find_files/search_text |
| `test_event_store.py`    | append, iter_all, filter, torn-line tolerance |
| `test_registry.py`       | register/get/resolve, дубли |
| `test_state_reset.py`    | safe workspace `.igla` reset and CLI command |
| `test_todo_tree.py`      | ветвление, агрегация статуса, snapshot round-trip |
| `test_policy_engine.py`  | unknown_tool, schema, allowed/forbidden, no_blind_retry, no user-contact before discovery |
| `test_prompts.py`        | prompt hot path: no repeated system message |
| `test_motivation.py`     | bootstrap, failure diagnosis, clarification, task done |
| `test_plain_cli.py`      | copyable plain output and event timeline |
| `test_planner_loop.py`   | end-to-end через OfflineCannedClient: declare/branch/clarify/diagnose, malformed question rejection |

Запуск:

```bash
PYTHONPATH=src python -m pytest tests/
```

## Что специально не реализовано в первом варианте

Согласно требованию пользователя — для первого этапа **намеренно отложено**:

* **Sandbox subsystem** (06-sandbox-security.md). Сейчас tools запускаются в процессе; манифест декларирует профиль, и кодовая дорога для будущей переадресации в bwrap/firejail/Docker готова, но не активна.
* **Memory subsystem** (05-memory-system.md). Только EventStore + Receipts.
* **Context Manager** (07-context-manager.md). Промпт ещё не делит контекст по уровням L0–L5.
* **Tool Forge** (09-tool-forge.md). Карантин не реализован.
* **Verifier / Rollback** (10-verification-rollback.md). Verifier ограничен schema_validation.

Эти подсистемы — следующие этапы. Архитектура (envelope'ы и API ядра) построена так, что они подключаются без перекладывания фундамента.
