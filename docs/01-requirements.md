# 01. Требования к системе

Этот документ фиксирует требования верхнего уровня. Уточнения по конкретным подсистемам — в соответствующих главах документации.

## 1. Заинтересованные стороны

| Роль | Интерес |
|------|---------|
| Главный пользователь (владелец машины) | Решение конкретных инженерных задач: Gentoo, Quickemu, vLLM, killswitch, Excel-анализ, code-repair |
| Главный ИИ (LLM-планировщик) | Получает структурированный TaskSpec и реестр capabilities; генерирует proposals |
| IGLA Runtime | Принуждает все правила; единственный субъект, который реально что-то делает |
| Авторы tools | Пишут модули по фиксированному протоколу |
| Будущая ИГЛА (саморасширение) | Через Tool Forge предлагает кандидатов в tools, проходит карантин |

## 2. Функциональные требования

### 2.1. Понимание задачи

- F1. Принимать запрос на естественном языке.
- F2. Превращать его в `TaskSpec` со структурой: цель, домен, риск, критерии успеха, разрешённые/запрещённые действия, политика контекста.
- F3. Выполнять «intake»: находить TODO/README/docs/issue-файлы перед изменением проекта.
- F4. Запрашивать уточнения, если задача неоднозначна или конфликтует с TODO.

### 2.2. Планирование

- F5. Строить план как DAG из `PlanStep`. Каждый шаг — типизированный, имеет `depends_on`, `requires`, `expected_outputs`, `verification`, `on_failure`.
- F6. Выбирать tools не по имени, а по `capability + constraints` через реестр.
- F7. Поддерживать перепланирование при провале: переход в режим `FAILURE_DIAGNOSIS_REQUIRED`.

### 2.3. Исполнение

- F8. Все вызовы tools идут через единый envelope `ToolInvocation` → `ToolResult`.
- F9. Все эффекты — через kernel effect tools (apply\_patch, run\_command, install\_package, restart\_container, write\_memory, …).
- F10. Все артефакты выдаются по ссылке (`artifact_id`), не по содержимому.
- F11. Все шаги выполняются в sandbox (Docker/firejail/nsjail/bubblewrap по уровню риска).

### 2.4. Память

- F12. Поддерживать шесть типов памяти: Event, Fact, Case, Project, Preference, Working Context.
- F13. Дедуплицировать память на четырёх слоях: canonical key, source hash, semantic, conflict.
- F14. Версионировать факты и помечать конфликты `MemoryConflict`.
- F15. Поддерживать Obsidian как **mirror**, не как первоисточник.

### 2.5. Контекст

- F16. Управлять контекстом по уровням L0–L5; модель никогда не получает «весь мир».
- F17. Большие артефакты обязаны проходить detеrministic pre-processing (профиль/индекс) перед тем, как их выдержки попадут в LLM.
- F18. Поддерживать Task Ledger и `state checkpoint` для долгих задач.

### 2.6. Верификация и обратимость

- F19. Каждый mutation требует verifier и плана отката до того, как он будет одобрен.
- F20. Любое утверждение в финальном отчёте имеет `evidence_refs`.
- F21. После провала verifier’а runtime инициирует rollback, если он возможен; иначе — переходит в `BLOCKED`.

### 2.7. Регистрация и саморасширение

- F22. Реестр инструментов хранит манифесты: capability, schemas, risks, policies, compatibility, verifier, sandbox profile.
- F23. Новые tools от LLM регистрируются только через карантинный pipeline (proposal → quarantine → tests → static checks → security review → promotion).

### 2.8. Аудит

- F24. Каждое действие, отказ, ошибка и результат пишутся в EventStore (append-only).
- F25. Возможность восстановить ход задачи по EventStore.

## 3. Нефункциональные требования

### 3.1. Безопасность

- N1. Модель не имеет прямого доступа к: файловой системе, shell, сети, контейнерам, БД, секретам, реестру, памяти, runtime state.
- N2. Action Gate Matrix — обязательная проверка предикатов перед каждым действием.
- N3. Все секреты хранятся зашифрованными (см. [06-sandbox-security.md](06-sandbox-security.md)). LLM не получает их в открытом виде; вместо них — ссылки/handles.
- N4. Sandbox по умолчанию: read-only filesystem, отключённая сеть, ограниченные ресурсы. Любая привилегия — явная.
- N5. AI-generated tools запрещают: subprocess, os.system, shell=True, socket/network, requests/httpx, чтение env вне whitelist, доступ за пределы workspace, удаление файлов, chmod/chown, sudo, docker socket, запись в registry/memory напрямую.

### 3.2. Надёжность и обратимость

- N6. Любое изменение файла — с backup и проверяемым diff.
- N7. Любое изменение системы (firewall, сервисы, контейнеры) — с auto-rollback по таймеру и healthcheck.
- N8. Стабильность runtime: kernel должен «не ошибаться» в смысле инвариантов, даже если orchestrator/LLM ошибается.

### 3.3. Производительность и ресурсы

- N9. Бюджеты на каждый шаг: `timeout_seconds`, `max_memory_mb`, `max_file_size_mb`. Превышение — failure.
- N10. Большие артефакты (>N MB или >N rows) обязаны идти через сжатие/индекс/профиль до LLM.
- N11. Контекстный бюджет модели не превышается: при заполнении выполняется `state checkpoint` и handoff.

### 3.4. Совместимость

- N12. Стабильный protocol\_version envelope’ов (ToolInvocation, ToolResult, ArtifactDescriptor, EventRecord, EvidenceRecord, PolicyDecision, MemoryRecord). Обратносовместимые изменения — minor; ломающие — major.
- N13. Adapters для конверсии артефактов между minor/major версиями.
- N14. Реализация модулей не должна зависеть от внутренних структур ядра — только от envelope’ов.

### 3.5. Прозрачность

- N15. Финальный отчёт содержит: цель, шаги, evidence, проверенные факты, неподтверждённые предположения, риски, что было откатано.
- N16. Любая запись в память сопровождается `source_events`, `confidence`, `scope`.

### 3.6. Локальность и приватность

- N17. По умолчанию ИГЛА не отправляет данные пользователя в сеть. Любые исходящие соединения — с явным разрешением и логированием.
- N18. Поддерживается работа с локальной LLM (vLLM/llama.cpp). Архитектура не привязана к конкретному провайдеру модели.
- N19. Vault/память шифруются at-rest для чувствительных scope’ов.

### 3.7. Стек

- N20. Базовый язык — Python (orchestrator, registry, schemas, memory, planner).
- N21. Pydantic — описание всех envelope’ов и schemas.
- N22. SQLite/Postgres — canonical memory + index.
- N23. Rust — низкоуровневые компоненты (sandbox runner, process supervisor, policy guard, log parser, fast pipelines), подключаются через PyO3.
- N24. Docker / firejail / nsjail / bubblewrap — sandbox profiles.

## 4. Глобальные инварианты (constitution)

Проверяемые правила, нарушение которых runtime обязан физически предотвращать. Полный список и реализация — в [03-kernel-and-policies.md](03-kernel-and-policies.md). Краткий перечень:

```
no_direct_tool_access        Модель только предлагает; tools запускает только Runtime.
step_barrier                 Следующий шаг невозможен без COMPLETED-результата зависимостей.
read_before_write            Файл нельзя менять без прочтения и совпадения хеша.
hash_before_patch            Каждый патч содержит base_sha256.
todo_before_execution        Без intake (TODO/README/docs) проект изменять нельзя.
no_blind_retry               После провала запрещён повтор того же класса действий до диагностики.
evidence_required            Любой claim ссылается на evidence.
context_budget_required      Большие артефакты идут не в контекст, а через индекс/профиль.
reversible_by_default        Mutating action требует backup, diff и план отката.
memory_dedupe_required       Запись в память без dedupe-проверки и source_event запрещена.
schema_validated             Любой ToolInvocation/ToolResult проходит валидацию схемы.
sandbox_required             Все execution-tools идут через sandbox с заявленным профилем.
no_unapproved_generated_tool Сгенерированный tool не регистрируется без прохождения карантина.
```

## 5. Out of scope (первой версии)

- Многопользовательский режим, ACL по пользователям.
- Полный распределённый запуск на нескольких хостах.
- UI-уровни выше CLI/локального web-API/Obsidian-зеркала.
- Автоматический Telegram/email-канал (возможен позже как отдельный adapter UI).
- Самообучение моделей (fine-tuning); ИГЛА работает с готовыми моделями.

## 6. Критерии приёмки MVP

Для оценки готовности первого релиза см. [11-roadmap.md](11-roadmap.md). Каждый MVP заканчивается набором демонстрационных сценариев, которые runtime должен пройти **без ручной правки** и с полным аудитом в EventStore.
