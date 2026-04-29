# 05. Система памяти

## 1. Зачем отдельная подсистема памяти

ИГЛА не должна «помнить» через чат-историю. Чат — это поток событий, а не знание. Знание появляется только после **дедупликации, проверки, scope’а и архивации**. Поэтому память — отдельный сервис со своим pipeline.

```
Контекст ≠ память.
Память ≠ лог.
Лог ≠ знание.
Знание = дедуплицированный, проверенный, scoped факт с историей источников.
```

## 2. Шесть типов памяти

```
1. Event Memory       — append-only журнал всего, что произошло
2. Fact Memory        — проверенные факты о системе (версии, пути, флаги, конфиги)
3. Case Memory        — решённые кейсы: симптом → причина → фикс → проверка
4. Project Memory     — состояние конкретных проектов (TODO, архитектура, deps, команды)
5. Preference Memory  — устойчивые предпочтения пользователя (стиль ответов, политика risk)
6. Working Context    — временная память текущей задачи (умирает или архивируется)
```

`Event Memory` — фундамент всего: из неё «дистиллируется» Fact/Case через MemoryService. Working Context — единственный тип, который может удаляться без архивации (но он не должен содержать ничего, чему не место в Event Memory).

## 3. Структура хранилища

```
.igla/memory/
├── facts.jsonl         # Fact memory (canonical)
├── cases.jsonl         # Case memory
├── projects.jsonl      # Project memory
├── preferences.jsonl   # Preference memory
├── working/            # активные working sets по task_id
├── conflicts.jsonl     # MemoryConflict записи
└── index/
    ├── canonical_keys.sqlite
    ├── vectors/
    └── fts/
```

EventStore хранится отдельно (`.igla/events.jsonl`). Память — продукт переработки EventStore.

## 4. MemoryCandidate / MemoryRecord

Запись в память не «через write\_text», а через pipeline кандидатов.

`MemoryCandidate`:

```json
{
  "type": "case",
  "scope": "project:nvidia-workbench-vllm",
  "title": "vLLM failed because --enable-ui is unsupported",
  "dedupe_key": "vllm:cli:unrecognized-argument:enable-ui",
  "content": {
    "symptom": "unrecognized arguments: --enable-ui",
    "cause": "Current vLLM CLI does not support this flag",
    "fix": "Remove --enable-ui from launch arguments",
    "verification": "vllm serve --help does not list --enable-ui"
  },
  "source_events": ["evt_001", "evt_002"],
  "confidence": "high"
}
```

`MemoryRecord`:

```json
{
  "memory_id": "mem_01HX...",
  "type": "case",
  "scope": "project:nvidia-workbench-vllm",
  "canonical_id": "case_vllm_enable_ui",
  "dedupe_key": "vllm:cli:unrecognized-argument:enable-ui",
  "status": "verified",
  "confidence": "high",
  "created_at": "...",
  "updated_at": "...",
  "source_events": ["evt_001", "evt_002"],
  "supersedes": [],
  "superseded_by": null,
  "ttl": null
}
```

`scope` — обязательное поле. Без scope факт не записывается. Примеры scope:

```
host:rog15
host:spark-gb10
project:quickemu-windows11
project:nvidia-workbench-vllm
container:workbench-vllm
user:default
global
```

`confidence`:

```
observed     — прямое наблюдение из локального источника
verified     — подтверждено отдельным экспериментом/документацией
inferred     — выведено из других фактов
external     — взято из внешнего источника без локальной проверки
```

## 5. MemoryService API

```python
class MemoryService:
    def propose(self, candidate: MemoryCandidate) -> MemoryDecision: ...
    def dedupe(self, candidate: MemoryCandidate) -> DedupeResult: ...
    def commit(self, candidate: MemoryCandidate) -> MemoryRecord: ...
    def retrieve(self, query: MemoryQuery) -> list[MemoryRecord]: ...
    def archive(self, record_id: str, reason: str) -> None: ...
    def supersede(self, old_id: str, new_record: MemoryRecord) -> None: ...
```

Записи в память напрямую не существует. Только через `propose → dedupe → commit`. Любой пропуск — отказ Kernel’a.

## 6. Дедупликация: четыре слоя

### Слой 1 — точная дедупликация по canonical key

```
canonical_key = scope + subject + predicate + object + validity
```

Пример:

```json
{
  "scope": "host:rog15",
  "subject": "quickemu",
  "predicate": "version",
  "object": "4.9.7"
}
```

Если `canonical_key` уже есть — не добавляем дубликат, просто обогащаем `source_events`.

### Слой 2 — source hash

Если ИГЛА снова прочитала тот же лог/файл/документ:

```
sha256(content) совпадает → не индексировать заново
```

Все `LogExcerpt`/`FileSnapshot` имеют content\_hash — это становится natural deduplication key.

### Слой 3 — semantic dedupe

Через embeddings. Если формулировки разные, а смысл один — создаём не новый факт, а новый source у существующего:

```
"vLLM does not support --enable-ui"
"Флаг --enable-ui отсутствует в текущей версии vLLM"
              ↓
fact_id остаётся прежним
sources += new_source
```

Порог сходства настраивается; ниже порога — потенциальный дубль уходит в очередь ручной проверки, не пишется автоматически.

### Слой 4 — конфликт-детектор

Если новый факт противоречит существующему:

```
old: vLLM supports --enable-ui (verified, scope:container:workbench-vllm@2025-12)
new: vLLM does not support --enable-ui (verified, scope:container:workbench-vllm@2026-04)
```

Создаётся `MemoryConflict`:

```json
{
  "type": "MemoryConflict",
  "subject": "vllm",
  "predicate": "supports_flag",
  "object": "--enable-ui",
  "old_value": true,
  "new_value": false,
  "scope": "container:workbench-vllm",
  "resolution_required": true
}
```

ИГЛА должна решить в явном пайплайне:

- версия объекта изменилась? → старая запись superseded\_by новой;
- старый факт был для другого scope? → уточнить scope;
- факт ошибочный? → archive(reason="incorrect");
- оба верны для разных условий? → разделить scope.

## 7. Retrieval

`MemoryQuery`:

```json
{
  "scope_hints": ["project:nvidia-workbench-vllm", "host:spark-gb10"],
  "subject_hints": ["vllm"],
  "tags": ["cli-error"],
  "semantic_query": "vLLM startup error with unrecognized argument",
  "max_results": 8,
  "min_confidence": "observed"
}
```

Стратегия:

1. fast path: точное совпадение по `dedupe_key`/`canonical_key`;
2. scope-фильтр: ограничиваем кандидатов;
3. FTS по title/content;
4. semantic re-rank через embeddings;
5. дедупликация в выдаче.

В контекст модели передаются только `MemoryRecord`-сводки (title + summary + scope + confidence + source\_events count), не «полное содержимое всей памяти».

## 8. Working Context (память текущей задачи)

```json
{
  "task_id": "task_123",
  "active_facts": ["fact_qemu_version", "fact_ovmf_path"],
  "active_files": ["windows-11.conf", "run-vm.sh"],
  "active_evidence": ["event_read_conf", "event_qemu_log"],
  "context_budget": {
    "max_tokens": 32000,
    "used_tokens": 12000
  },
  "open_questions": [],
  "rejected_hypotheses": []
}
```

Working Context **не** записывается в Fact/Case память автоматически. После завершения задачи Memory Curator решает, что распределить:

- факты с confidence ≥ observed и подтверждённым source\_event → Fact Memory;
- решённые проблемы → Case Memory;
- состояние проекта → Project Memory;
- стилевые предпочтения пользователя → Preference Memory;
- остальное → archive (`90_Inbox`/`60_Archive`).

## 9. Obsidian как mirror, не источник

Canonical store — SQLite/JSONL. Obsidian — человеко-читаемая витрина и редактор.

```
IGLA-Vault/
├── 00_Index/
│   ├── System Map.md
│   ├── Active Projects.md
│   └── Known Risks.md
│
├── 10_Hosts/
│   ├── rog15.md
│   └── spark-gb10.md
│
├── 20_Projects/
│   ├── quickemu-windows11.md
│   ├── nvidia-workbench-vllm.md
│   └── llama-cpp-gb10.md
│
├── 30_Cases/
│   ├── vllm-enable-ui-error.md
│   ├── quickemu-ovmf-paths.md
│   └── nftables-tun0-killswitch.md
│
├── 40_Tools/
│   ├── inspect_vllm_env.md
│   ├── validate_quickemu_config.md
│   └── analyze_nft_killswitch.md
│
├── 50_Recipes/
│   ├── start-vllm-model.md
│   ├── test-vm-killswitch.md
│   └── build-llama-cpp-cuda.md
│
├── 60_Archive/
│   └── old/
│
└── 90_Inbox/
    └── unprocessed.md
```

YAML front matter обязателен:

```yaml
---
memory_id: mem_001
type: case
scope: project:nvidia-workbench-vllm
canonical_id: case_vllm_enable_ui
dedupe_key: vllm:cli:unrecognized-argument:enable-ui
status: verified
confidence: high
created: 2026-04-29
updated: 2026-04-29
source_events:
  - evt_123
  - evt_124
tags:
  - vllm
  - workbench
  - cli-error
---
```

Если человек правит Markdown — это идёт через **import pipeline**, который заново пропускает запись через dedupe/conflict, не меняет canonical напрямую. Markdown — это «view», canonical — это «model».

## 10. Pipeline записи

```
raw event in EventStore
        ↓
Memory Curator extracts MemoryCandidate
        ↓
canonical_key check (Layer 1)
        ↓
source hash check (Layer 2)
        ↓
semantic dedupe (Layer 3)
        ↓
conflict check (Layer 4)
        ↓
PolicyEngine: memory_dedupe_required, source_event_required
        ↓
commit → MemoryRecord
        ↓
Obsidian mirror writer
        ↓
index update (canonical / vector / FTS)
```

Каждый этап логируется в EventStore: можно потом восстановить, почему запись была сделана/отклонена.

## 11. Сбор Working Context из Памяти и Контекста (источники истины)

Во время работы у ИГЛЫ есть два «места информации»:

- **рабочий контекст модели** (то, что она «видит сейчас»);
- **постоянная память** (то, что было сохранено).

Чтобы они не дублировались, действует жёсткое правило:

```
Перед записью кандидата в память Memory Curator проверяет:
1. совпадает ли он с любым активным элементом Working Context;
2. совпадает ли он с любым уже существующим MemoryRecord;
3. если совпадает — обновляется только source_events существующей записи.
```

Так, даже если LLM «помнит» факт по контексту и пытается записать его в память, MemoryService не пропустит дубль.

## 12. TTL и архивация

Не вся память живёт вечно. Поля в `MemoryRecord`:

```
ttl                   — опционально; например, 30/60/180 дней для волатильных фактов
volatility            — stable | volatile | versioned_object
deprecation_signal    — superseded_by, archived, refuted
```

Стандартные правила:

- `Fact{type=software_version}` → volatility=versioned\_object, требует пересмотра при апдейте версии;
- `Case{type=transient_workaround}` → ttl=90d;
- `Preference` → нет ttl, но обновляется по новому источнику.

Archive не удаляет запись — переносит её в `60_Archive/` с указанием причины.

## 13. Безопасность памяти

- Чувствительные scope’ы (`secrets`, `creds`, `private`) шифруются at-rest (см. [06-sandbox-security.md](06-sandbox-security.md)).
- В контекст LLM такие записи попадают только как handles (`secret_ref:...`), а не значения.
- Tool Forge не имеет доступа к памяти иной, чем ему явно разрешённой policy’ой.
- LLM не может написать в память напрямую — только через предложенный `MemoryCandidate`, который Kernel пропустит через dedupe и проверку scope.

## 14. Минимальный «первый рабочий слой» памяти

Для MVP-3 достаточно:

```
events.jsonl
facts.jsonl
cases.jsonl
canonical_keys.sqlite
```

Без vector index, без Obsidian mirror, без TTL. Это уже даёт `read_before_write`-проверку, dedupe layer 1+2 и базовый retrieval. Остальные слои подключаются по плану из [11-roadmap.md](11-roadmap.md).
