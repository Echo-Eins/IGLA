# 11. Дорожная карта

Цель — собрать ИГЛУ инкрементально, начиная с **тюремщика для LLM**, а не с самой LLM. Каждый MVP заканчивается рабочей системой, которую можно использовать в ограниченном сценарии и которая является основой для следующего этапа.

Главная формула:

```
Сначала Kernel.
Потом Tools.
Потом Event log.
Потом Verifiers.
Потом Memory.
Потом LLM.
Потом Generated tools.
```

## MVP-0. Read-only анализатор без LLM

**Цель:** строгий исполнитель действий с минимумом политик. LLM ещё нет.

Объём:

- `Kernel`: ReceiptManager, базовый PolicyEngine (path\_allowed, file\_read\_before\_write, no\_blind\_retry — упрощённо).
- `Tools`: `read_file`, `list_dir`, `inspect_file_meta`, `inspect_command_help`.
- `EventStore`: JSONL append-only.
- `Workspace`: жёсткий ограниченный workspace.
- `CLI`: `igla run task.json`.

Демо-сценарий:

```
1. task.json просит прочитать файл, потом «попатчить» — runtime разрешает чтение, отвергает патч.
2. task.json просит прочитать, потом read_again того же файла после изменения — runtime детектит stale receipt.
```

Критерий приёмки: ни одна мутация невозможна. Все события логируются. Все запреты работают.

## MVP-1. Policy Kernel + reversible edits

**Цель:** добавить mutating-инструменты под жёсткой policy.

Объём:

- `Tools`: `patch_file` (с backup, base\_sha256), `restore_backup`, `run_command` в read-only sandbox.
- `Sandbox`: `read_only_file_access`, `write_workspace` профили (через bwrap или firejail).
- `RollbackManager`: snapshot/plan/execute/discard.
- `Policy`: `read_before_write`, `hash_before_patch`, `backup_before_mutation`, `timeout_required_for_execution`.
- `Verifier`: `schema_validation` и `command` checks.

Демо-сценарий:

```
1. patch_file без read_file → DENY.
2. patch_file с read_file → backup создан, diff применён.
3. patch_file → verifier по diff → ок.
4. run_command в sandbox с таймаутом → ok.
5. при failure → откат и режим FAILURE_DIAGNOSIS_REQUIRED.
```

Критерий приёмки: любая мутация обратима, любой failure не оставляет систему в «грязном» состоянии.

## MVP-2. Tool Protocol + Registry + Artifacts

**Цель:** заморозить envelope’ы, ввести типизированные артефакты.

Объём:

- Pydantic-модели envelope’ов (см. [04-tool-protocol.md](04-tool-protocol.md)).
- `ToolRegistry` с manifest’ами; resolve по capability+constraints.
- `ArtifactStore`: put/get/open/derive; контентные хеши.
- Адаптация существующих tools под envelope-протокол.
- Базовые artifact types: `FileSnapshot`, `CommandOutput`, `LogExcerpt`.

Демо-сценарий:

```
1. Plan → ToolInvocation → ToolResult (всё валидируется по schema).
2. Tool A производит FileSnapshot, Tool B потребляет его по artifact_id.
3. Версионирование: minor bump tool.input.schema → совместимость сохраняется.
```

Критерий приёмки: между tools ходят только envelope’ы и артефакты. Прямые вызовы между модулями технически невозможны.

## MVP-3. Memory System (без Obsidian)

**Цель:** ввести EventStore→Memory pipeline, дедупликацию.

Объём:

- `MemoryService`: propose/dedupe/commit/retrieve/archive.
- Хранилища: `facts.jsonl`, `cases.jsonl`, `canonical_keys.sqlite`.
- Дедупликация Layer 1 (canonical key) + Layer 2 (source hash).
- Базовый retrieval по scope+subject+tags.
- Working Context per task.

Демо-сценарий:

```
1. Task завершилась → Memory Curator извлёк MemoryCandidate → commit.
2. Повторная задача с тем же симптомом → retrieval нашёл старый case → ускорил решение.
3. Дубль не записывается; new source добавляется к существующей записи.
```

Критерий приёмки: Memory не может быть записана без `source_event` и dedupe-проверки. Retrieval отдаёт релевантные кейсы.

## MVP-4. Context Manager + LLM Planner

**Цель:** подключить LLM как планировщик. Контекст-менеджер обязателен.

Объём:

- `ContextManager`: уровни L0–L5, бюджеты, evict/compact.
- `LLMClient` интерфейс; адаптеры для как минимум одной локальной (vLLM/llama.cpp) и одной облачной модели.
- Strict structured output: модель возвращает только Proposal по schema.
- Allowed/Forbidden Next Actions.
- Реакция на rejection (re-loop).
- Hypothesis Engine + Experiment Designer (минимально).

Демо-сценарий:

```
1. Пользователь: «почему vLLM не стартует?» → ИГЛА собирает state → строит гипотезы → запускает experiments → формирует план починки.
2. После провала шаг runtime блокирует patch → модель идёт в memory/docs → новая гипотеза → новый план.
3. Финальный отчёт с evidence_refs.
```

Критерий приёмки: модель не имеет возможности обойти allowed actions. Любой proposal проходит через PolicyEngine.

## MVP-5. Domain Packs

**Цель:** прикладная польза для целевых доменов пользователя.

Объём (по один пак за итерацию):

```
igla-pack-vllm
igla-pack-quickemu
igla-pack-nftables
igla-pack-docker
igla-pack-gentoo
igla-pack-llama-cpp
igla-pack-python-projects
igla-pack-excel
```

Каждый пак — это:

- набор tools (pure + узкие effect через kernel);
- artifact types;
- verifiers под домен;
- recipes в Memory;
- known failure patterns.

Демо-сценарий: один из реальных кейсов пользователя (например, поднять vLLM с MiniMax и подобрать профиль) проходится с минимальным вмешательством человека.

## MVP-6. Obsidian Mirror и Excel Pipeline

**Цель:** довести «человеко-читаемый» слой памяти и работу с большими табличными артефактами.

Объём:

- Mirror Writer: Markdown export из MemoryRecord; YAML front matter; каталоги 00–90.
- Import Pipeline: ручные правки md → MemoryCandidate → dedupe.
- Excel pipeline: `register_file_artifact`, `profile_excel`, `detect_excel_anomalies`, `cluster_columns`, `generate_markdown_report`.
- Запрет на пересылку Excel в LLM — реализован policy-level.

Демо-сценарий:

```
1. Пользователь даёт Excel 2k×700 → ИГЛА выдаёт ExcelProfile + AnomalyReport + targeted slices.
2. LLM получает только сводки и срезы; runtime блокирует попытку «загрузить весь файл».
3. Memory обновлена; Obsidian mirror содержит case с правильным front matter.
```

## MVP-7. Tool Forge

**Цель:** включить безопасное саморасширение.

Объём:

- Quarantine pipeline (см. [09-tool-forge.md](09-tool-forge.md)).
- Static checks, sandbox run, security review.
- Только pure tools.
- Approval gate 1 и 2.

Демо-сценарий:

```
1. Planner: «не хватает validate_quickemu_config».
2. Forge: proposal → quarantine → tests → checks → human approve → registry.
3. Tool используется в следующей задаче.
```

## MVP-8. Rust Core

**Цель:** перенести критичные части ядра в Rust.

Объём:

- `sandbox_runner` (Rust + PyO3).
- `process_supervisor` (timeout/kill/log streaming).
- `policy_guard` (быстрые предикаты, blacklist tokens).
- `log_parser` (быстрый парсинг больших логов).
- `policy_kernel` (часть PolicyEngine).

Критерий приёмки: Python API не меняется, Rust-реализация даёт предсказуемую производительность и устраняет гонки в supervisor’е.

## MVP-9. Encryption и Secrets

**Цель:** ввести секреты и шифрование at-rest.

Объём:

- `secrets/store.enc` через age/libsodium.
- handles в ToolInvocation.
- Шифрование sensitive scope’ов памяти.
- Recovery flow (восстановление по passphrase).

## MVP-10. Experiment Engine

**Цель:** сравнительные эксперименты как первый класс.

Объём:

- ABTestArtifact, BenchmarkProfile.
- Tools для запуска одной модели/конфигурации, измерения tokens/sec, VRAM, latency.
- Сравнение профилей и сохранение «победителя» в Memory.

Демо-сценарий: «сравни llama.cpp и vLLM на этой модели» → детерминированный отчёт + сохранённый профиль.

---

Дорожная карта — не план «когда сделать», а порядок зависимостей. Каждый этап опирается на инварианты предыдущего. Ничего нельзя «перепрыгнуть»: пропустишь Memory — сломается dedupe; пропустишь Verifier — сломается evidence; пропустишь Sandbox — сломается всё.

Главный совет, который проходит сквозь весь roadmap:

```
Не пиши «ИГЛА». Пиши маленький запрет.
Каждую неделю — один новый инвариант.
```
