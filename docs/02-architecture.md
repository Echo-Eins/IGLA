# 02. Архитектура верхнего уровня

## 1. Слои системы

```
┌─────────────────────────────────────────────────────────────┐
│                          IGLA UI                            │
│       CLI · Local Web · Obsidian Mirror · Future Adapters   │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                      IGLA Orchestrator                      │
│  Task Intake · Planner · Hypothesis Engine · Context Mgr    │
│  Report Builder · Memory Curator · LLM client(s)            │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                        IGLA Kernel                          │
│  Policy Engine · State Machine · Tool Registry · Executor   │
│  Artifact Store · Evidence Store · Event Store · Verifier   │
│  Rollback Manager · Receipt Manager · Schema Validator      │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│                    Tools & Sandbox Runtime                  │
│  Pure tools (read, parse, profile, classify, generate)      │
│  Effect tools (apply_patch, run_command, install, restart)  │
│  Sandbox profiles (Docker / firejail / nsjail / bwrap)      │
└─────────────────────────────────────────────────────────────┘
```

Главное правило слоёв:

```
Orchestrator может ошибаться.
Kernel не должен ошибаться.
```

Поэтому:

- **UI** — без бизнес-логики, только адаптеры ввода/вывода.
- **Orchestrator** — самое «умное», но никогда не имеет прямого доступа к мутациям. Использует Kernel API.
- **Kernel** — пишется как набор детерминированных сервисов. На него опирается всё остальное. Со временем критичные части (sandbox, policy guard, process supervisor) выносятся в Rust через PyO3.
- **Tools/Sandbox** — изолированные процессы. Tool «не знает» про другие tools, про память, про state.

## 2. Поток управления

Целевой цикл (`IGLA Loop`):

```
User Request
    ↓
Task Intake             → TaskSpec
    ↓
Memory Retrieval        → relevant facts/cases
    ↓
Observation             → ObservationArtifacts (env state, files, configs)
    ↓
Hypothesis Engine       → ranked hypotheses
    ↓
Experiment Designer     → safe minimal experiments
    ↓
Plan Proposal (LLM)     → DAG of PlanSteps
    ↓
Policy Validation       → allow / deny / needs_approval
    ↓
Step Execution          → ToolInvocation in sandbox
    ↓
Evidence Capture        → events, artifacts, logs
    ↓
Verifier                → success criteria check
    ↓
Memory Distillation     → MemoryCandidate → dedupe → commit
    ↓
Report Builder          → final answer with evidence
```

Ни один шаг не выполняется без перехода через Kernel.

## 3. Orchestrator: что внутри

`igla/orchestrator/` отвечает за:

- **Task Intake** — нормализация запроса в `TaskSpec`, обнаружение TODO/README/docs.
- **Planner** — превращение TaskSpec + observed state + memory hits в `Plan` (DAG из `PlanStep`).
- **Hypothesis Engine** — генерация и ранжирование гипотез о причине проблемы (для диагностических задач).
- **Experiment Designer** — построение минимальных проверочных команд для подтверждения/опровержения гипотез.
- **Context Manager** — управление контекстным бюджетом, handoff/checkpoint, выбор уровней L0–L5.
- **Memory Curator** — приоритизация retrieval, дедупликация на запись (через MemoryService).
- **Report Builder** — финальный отчёт с evidence и предупреждениями о рисках.
- **LLM Client(s)** — единая обёртка над разными моделями (большая LLM, малая локальная LLM, embeddings).

Orchestrator **никогда** не вызывает tools напрямую. Он формирует `ToolInvocation` и отдаёт его в Kernel.

## 4. Kernel: что внутри

`igla/kernel/` — детерминированные сервисы:

- **PolicyEngine** — проверка предикатов; возвращает `PolicyDecision` (allow / deny / needs\_approval / needs\_diagnosis / needs\_more\_context / needs\_memory\_search / needs\_documentation\_search).
- **StateMachine** — статусы шагов и режимов задачи (`READY`, `WAITING_FOR_TOOL_RESULT`, `FAILURE_DIAGNOSIS_REQUIRED`, `BLOCKED`, `TASK_DONE`).
- **ToolRegistry** — каталог манифестов; разрешение по capability+constraints; валидация input/output schema.
- **Executor** — единый вход для запуска tools в sandbox; нормализация ошибок.
- **ArtifactStore** — постоянное хранилище типизированных артефактов; immutable; адресация по `artifact_id` + `content_hash`.
- **EvidenceStore** — типизированные evidence-записи, привязанные к событиям.
- **EventStore** — append-only журнал событий (JSONL/SQLite); основа аудита и памяти.
- **VerifierService** — выполнение verifier по `VerificationSpec` шага.
- **RollbackManager** — снапшоты, бэкапы, контракты отката.
- **ReceiptManager** — read receipts, base hashes.
- **SchemaValidator** — JSON Schema / Pydantic-валидация envelope’ов и input/output.
- **PolicyKernel (Rust, поздняя стадия)** — критичные предикаты и proc supervisor с предсказуемыми гарантиями.

## 5. Tools и sandbox

См. [04-tool-protocol.md](04-tool-protocol.md) и [06-sandbox-security.md](06-sandbox-security.md).

Tool делится на:

- **Pure tools** — без побочных эффектов: `parse_log`, `profile_excel`, `detect_anomalies`, `generate_patch`, `summarize_document`, `classify_error`, `inspect_*`, `read_file`.
- **Effect tools** — меняют мир: `apply_patch`, `run_command`, `install_package`, `restart_container`, `modify_firewall`, `write_memory`. Только в ядре или под жёсткой policy.

AI-generated tools на ранних стадиях допускаются **только** как pure tools (см. [09-tool-forge.md](09-tool-forge.md)).

## 6. Хранилища

```
.igla/
├── events.jsonl                # EventStore (append-only)
├── state.json                  # текущее состояние runtime
├── receipts/                   # read receipts по задачам
├── artifacts/                  # типизированные артефакты
│   ├── command_outputs/
│   ├── file_snapshots/
│   ├── reports/
│   └── excel_profiles/
├── memory/
│   ├── facts.jsonl
│   ├── cases.jsonl
│   ├── projects.jsonl
│   ├── preferences.jsonl
│   ├── conflicts.jsonl
│   └── index/                  # vector / FTS / canonical key
├── secrets/                    # encrypted-at-rest, не доступен LLM
├── obsidian-mirror/            # md-зеркало памяти (read-only target)
└── quarantine/                 # AI-generated tools на проверке
```

См. подробнее в [05-memory-system.md](05-memory-system.md) и [06-sandbox-security.md](06-sandbox-security.md).

## 7. Внешние модели

Архитектура не привязана к конкретному провайдеру:

- **Большая LLM** — сложное планирование, объяснение, разбор unstructured-выдачи. Может быть Claude/GPT/локальная open-weights.
- **Малая локальная LLM** — классификация ошибок, routing capability, маленькие генерации.
- **Embeddings** — retrieval по памяти, поиск похожих кейсов, semantic dedupe.
- **Детерминированный код** — основной «вычислитель»: всё, что можно посчитать без модели, считается без модели.

Связь — через единый `LLMClient`-интерфейс, который Orchestrator использует «безмодельно» (не зная провайдера).

## 8. Минимальный технологический стек

| Компонент | Стек |
|-----------|------|
| Orchestrator, planner, registry, schemas | Python 3.12+, Pydantic v2 |
| API/UI | FastAPI (локальный), CLI (Typer/Click) |
| Хранилище | SQLite на старте, опционально Postgres + pgvector |
| Векторный поиск | локальный (FAISS/Chroma) или встроенный SQLite vector |
| Sandbox | Docker (профиль по умолчанию), firejail/nsjail/bubblewrap для лёгких задач |
| Rust core | sandbox\_runner, process\_supervisor, policy\_guard, log\_parser, fast pipelines (PyO3) |
| Mirror | Markdown (Obsidian-совместимый vault) |

## 9. Принципы развития архитектуры

- **Сначала ядро, потом всё остальное.** Первые недели — `read_file`, `patch_file`, `run_command` под policy. Никакой LLM, никакого Obsidian, никакой генерации модулей.
- **Никаких boundary skips.** Любой обход слоя (например, оркестратор пишет в FS напрямую) — баг архитектуры.
- **Растим вверх, а не вширь.** Лучше довести инвариант до проверяемого состояния, чем добавить ещё пять полусырых tools.
- **Заменяемость моделей.** Вся инфраструктура должна работать на полностью локальной open-weights LLM не хуже, чем на облачной.
