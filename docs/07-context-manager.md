# 07. Context Manager и работа с большими артефактами

## 1. Главное правило

```
В модель нельзя пихать весь мир.
Модель получает только рабочий пакет контекста.
```

Для этого есть отдельный сервис **Context Manager**, который:

- знает текущий контекстный бюджет модели;
- знает, какие артефакты уже видны и в какой форме (полная/summary/выдержка);
- решает, что включить в очередной запрос к LLM;
- инициирует профилирование/индексацию больших артефактов;
- делает `state checkpoint` при переполнении и handoff.

## 2. Уровни контекста

```
L0  текущее задание и критерии успеха
L1  последние действия и результаты (последние N PlanStep, ToolResult)
L2  релевантные выдержки из файлов (LogExcerpt, FileSnapshot summaries)
L3  краткая карта проекта (Project Memory summary)
L4  retrieved memory (Fact/Case/Preference, отфильтрованная по релевантности)
L5  полные артефакты — не передаются, доступны только через tools
```

Контекст модели формируется как **context\_pack**:

```json
{
  "context_pack": {
    "task": {...},
    "current_state": {...},
    "relevant_evidence": [...],
    "relevant_memory": [...],
    "open_questions": [...],
    "allowed_next_actions": [...],
    "forbidden_next_actions": [...]
  }
}
```

Если модели нужен полный артефакт — она не получает его в контекст, а посылает Kernel’у предложение:

```json
{
  "action": "read_artifact",
  "artifact_id": "art_excel_profile_001",
  "view": "section/columns_quality"
}
```

Kernel возвращает запрошенный срез/секцию.

## 3. Большие табличные артефакты (Excel и т.п.)

Прямой запрет: нельзя загрузить Excel и скормить его LLM целиком. Для таблиц 2k×700 это просто мусорная архитектура.

Целевой pipeline:

```
Excel file
    ↓
register_file_artifact         → ExcelWorkbook (artifact)
    ↓
profile_excel                  → ExcelProfile (artifact)
    ↓
detect_excel_anomalies         → ExcelAnomalyReport (artifact)
    ↓
cluster_columns                → ColumnClusterReport (artifact)
    ↓
LLM получает: profile summary + anomaly summary + targeted slices
```

`ExcelProfile` детерминированно содержит:

```
- список листов;
- размеры;
- названия столбцов;
- типы данных;
- процент пустых;
- уникальные значения (top-N);
- min/max/mean для чисел;
- частотные значения для категорий;
- формулы;
- дубликаты;
- выбросы;
- корреляции (опционально);
- подозрительные столбцы;
- группы похожих колонок;
- sample rows (N рандомных и N с аномалиями);
- data quality report.
```

LLM не считает таблицу. LLM **интерпретирует** уже посчитанные факты.

## 4. Большие текстовые артефакты (логи, документы)

Pipeline аналогичный:

```
log file
    ↓
register_file_artifact         → LogFile artifact
    ↓
parse_log                      → ParsedLog artifact
    ↓
classify_log_events            → LogEventStats artifact
    ↓
extract_relevant_excerpts      → list[LogExcerpt]
    ↓
LLM получает: stats summary + excerpts с границами
```

`LogExcerpt` всегда указывает позиции (`byte_start`/`byte_end`/`line_start`/`line_end`) и `sha256`, чтобы дальнейшие шаги могли соотносить выдержку с оригиналом.

## 5. Большие кодовые проекты

Для code-repair/анализа:

```
project tree
    ↓
build_project_index            → ProjectIndex artifact (files, modules, exports)
    ↓
extract_test_failures          → TestFailureBundle artifact
    ↓
collect_traceback_context      → TracebackContextBundle (files involved + lines)
    ↓
LLM получает: failure summary + minimal code excerpts + memory hits
```

ИГЛА не загружает в контекст «весь репозиторий». Она запрашивает срезы по конкретным символам/файлам через tools.

## 6. Контекстный бюджет

```json
{
  "model": "main_planner",
  "context_window_tokens": 128000,
  "reserved_for_output": 16000,
  "soft_budget": 80000,
  "hard_budget": 100000
}
```

`Context Manager` следит за:

```
allocated_l0  task / criteria
allocated_l1  recent actions
allocated_l2  evidence excerpts
allocated_l3  project map
allocated_l4  retrieved memory
overhead      system instructions, schema definitions, etc.
```

Когда `allocated >= soft_budget`:

- `compact_l1` — ужать историю шагов до summary;
- `summarize_l4` — заменить полные MemoryRecord на 1-строчные сводки;
- `evict_l2` — оставить только evidence, на которое есть active references.

При достижении `hard_budget` запрещены любые расширения контекста до `state checkpoint`.

## 7. State checkpoint и handoff

При переполнении или при смене этапа задачи:

```json
{
  "checkpoint_id": "cp_01HX...",
  "task_id": "task_123",
  "goal": "Fix vLLM startup",
  "completed_steps": ["step_1", "step_2", "step_3"],
  "failed_steps": ["step_4"],
  "current_blockers": ["unrecognized argument: --enable-ui"],
  "verified_facts": ["fact_vllm_no_enable_ui"],
  "unverified_assumptions": ["fact_cuda_ok"],
  "active_artifacts": ["art_vllm_log_001"],
  "next_allowed_actions": ["inspect_help", "search_memory"],
  "forbidden_actions": ["patch_start_script", "rerun_same_command"]
}
```

После checkpoint модель продолжает с «чистым» контекстом, но без потери состояния. Возможен и handoff между LLM (большая → малая, или наоборот) — checkpoint самодостаточен.

## 8. Task Ledger

В долгих задачах ИГЛА ведёт `Task Ledger` — постоянный объект, хранящийся в `.igla/state/tasks/{task_id}.json`:

```
- цель и критерии;
- открытые вопросы;
- выполненные шаги;
- провалившиеся шаги;
- текущие гипотезы и их статус;
- собранные evidence (refs);
- запреты текущего режима;
- следующий разрешённый шаг.
```

Ledger обновляется на каждом StateMachine-переходе. Это «единственная правда» о ходе задачи; контекст модели — лишь её срез.

## 9. Retrieval и rate-limit на память

Память не «всасывается» в контекст. На каждый шаг Memory Retrieval ограничен:

```
max_records_per_step: 8
max_tokens_per_record: 200
must_include: <records related to current evidence>
```

Если retrieval превышает бюджет — поднимаются только наиболее релевантные (canonical match → semantic match). Остальные доступны через явный `search_memory` tool call.

## 10. Дедупликация контекста и памяти

Перед формированием context\_pack Context Manager проверяет:

- нет ли среди ретривнутых MemoryRecord’ов записей, которые уже представлены в active evidence;
- нет ли в L2 выдержек, повторяющих уже включённый артефакт;
- нет ли двух эквивалентных описаний одного факта (canonical key или semantic similarity ≥ threshold).

Если есть — оставляется один, остальные заменяются ссылкой.

## 11. Уточняющие вопросы и intake

При неполном TaskSpec ИГЛА не «угадывает». Она задаёт уточнения через UI и хранит ответы в Working Context. Уточнения — отдельный класс шагов:

```
ask_user_clarification
ask_user_choice
ask_user_approval
```

Эти шаги не блокируются stepwise barrier’ом, но требуют явной user interaction и попадают в EventStore.

## 12. Резюме

Контекст-менеджер делает три вещи:

1. **Не пускает** большие артефакты в LLM; вместо них — детерминированный профиль/индекс.
2. **Сжимает и обновляет** активный контекст по мере работы; держит бюджеты.
3. **Сохраняет state** в Task Ledger и checkpoint’ах, чтобы задача не «забывалась» при сменах модели или при компакции.

Без такого слоя ИГЛА не сможет работать с реалистичными артефактами и быстро упрётся в окно контекста.
