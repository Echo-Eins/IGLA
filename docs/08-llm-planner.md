# 08. Главный ИИ: роль, мотивация, ограничения

## 1. Что делает LLM

LLM в ИГЛЕ — это **заменяемый планировщик**, а не исполнитель. Конкретно она отвечает за:

```
1. Понимание кривого человеческого запроса.
2. Сборку TaskSpec и уточнения.
3. Генерацию гипотез и их ранжирование.
4. Дизайн минимальных экспериментов.
5. Построение Plan (DAG из PlanStep).
6. Подбор capability + constraints для Tool Registry.
7. Аргументацию proposals (зачем этот шаг).
8. Чтение результатов и обновление гипотез.
9. Формирование финального отчёта.
```

LLM **не**:

```
- запускает tools напрямую;
- пишет в файлы, FS, БД, память;
- вызывает shell, сеть, контейнеры;
- меняет state runtime;
- регистрирует новые tools;
- решает, можно ли сделать действие.
```

## 2. Каркас взаимодействия

Каждый цикл с моделью имеет фиксированную форму:

```
Вход:
  - context_pack (см. 07-context-manager.md)
  - allowed_next_actions
  - forbidden_next_actions
  - mode (READY / FAILURE_DIAGNOSIS_REQUIRED / NEEDS_USER_APPROVAL / ...)
  - last_rejection (если прошлая попытка была отклонена политикой)

Выход:
  - Proposal (один из allowed actions)
  - reason
  - structured arguments
```

Proposal — строго JSON по фиксированной схеме. Для большинства провайдеров это делается через structured outputs / tool calling.

```json
{
  "action": "tool_invocation",
  "tool": {"name": "profile_excel", "version": "1.2.0"},
  "input": {
    "workbook_artifact_id": "art_workbook_001",
    "profile_level": "full"
  },
  "reason": "Need a deterministic profile before any anomaly detection",
  "expected_outputs": [
    {"artifact_type": "ExcelProfile", "schema_version": "1.x"}
  ],
  "verification_idea": "Schema validation of ExcelProfile artifact"
}
```

Любой proposal проходит через Policy Engine. Если отклонён — модель получает структурированную причину и выбирает другой allowed action.

## 3. Мотивация и ограничения

Мотивация задаётся не уговорами, а **средой**. Принципы:

1. **Single source of truth — Runtime.** Что разрешено сейчас, что запрещено — модель узнаёт от Kernel, а не из своего «здравого смысла».
2. **Allowed Next Actions** — буквальный список того, что модель может попросить. Любое «творчество» вне списка → отказ.
3. **Reject loop вместо штрафа.** Отказ не наказывает модель, а возвращает ей структурированный feedback (`rejection`, `reason`, `required_next_action`).
4. **Evidence-first.** Финальный ответ принимается, только если в нём есть `evidence_refs`. Без них — отказ и переход в evidence-сбор.
5. **Failure diagnosis required.** После провала модель физически не может попросить повтор — только diagnostic actions.
6. **Context-budget aware.** Модель видит бюджет; на её proposal’ы влияет правило «нельзя пихать большой артефакт в контекст».
7. **Modesty-by-design.** Любое утверждение о состоянии системы должно быть либо подтверждено tool output’ом, либо помечено как `assumption`.

## 4. Системные инструкции (system prompt)

System prompt держится коротким и **не** дублирует то, что уже принуждается runtime’ом. Его суть:

```
You are the IGLA Planner.
You do not execute tools. You only emit structured proposals.
Each proposal must be one of allowed_next_actions provided by Runtime.
Each claim about the system must reference evidence.
After a failure, you must diagnose before retrying.
You must not include large artifacts in your output;
  reference them by artifact_id and ask Runtime to read slices.
You must not invent facts about hosts, files, processes,
  versions, configs — observe them through tools first.
```

Любые «softer» инструкции (стиль, формат отчёта) приходят как часть `context_pack`, а не зашиты в system prompt.

## 5. Pre/Post hooks

Перед отправкой запроса в LLM Orchestrator:

- собирает context\_pack (Context Manager);
- ретривит память (Memory Service);
- помещает actions matrix;
- добавляет last\_rejection, если есть;
- валидирует размер по бюджету.

После получения ответа:

- валидирует JSON по schema;
- проверяет, что action в allowed\_next\_actions;
- передаёт proposal в Policy Engine;
- логирует в EventStore (`llm_proposal_received`, `policy_decision`).

## 6. Несколько моделей

Архитектура поддерживает разделение труда:

| Роль | Модель |
|------|--------|
| Большой планировщик / разбор unstructured / отчёт | Большая LLM (локальная или облачная) |
| Классификатор ошибок / capability routing | Малая локальная LLM |
| Embeddings для retrieval/dedupe | Локальная embedding-модель |
| Деттерминированный код | Сначала, везде где можно |

Все модели подключаются через единый `LLMClient`-интерфейс. Замена одного провайдера на другой — конфигурация, а не переписывание Orchestrator.

## 7. Поведение при провале

Когда tool/verifier возвращает failure:

1. Runtime переводит задачу в `FAILURE_DIAGNOSIS_REQUIRED`.
2. Модели в следующем запросе:
   - `last_error` со структурированным описанием;
   - allowed\_next\_actions содержит только diagnostic actions;
   - forbidden\_next\_actions содержит patch/install/restart/final\_answer\_success.
3. Модель обязана выдать одно из:
   - `read_logs`;
   - `inspect_state`;
   - `search_memory`;
   - `search_docs`;
   - `classify_error`;
   - `propose_minimal_experiment`;
   - `ask_user_if_blocked`.
4. Только после `failure_classified=true` и появления нового evidence runtime разрешает выйти из режима.

## 8. Поведение при неполном TaskSpec

Если TaskSpec неоднозначен или конфликтует с TODO:

```
mode = NEEDS_USER_CLARIFICATION
allowed_next_actions = [ask_user_clarification, ask_user_choice, search_project_docs]
```

Модель не имеет права «угадать» цель. Она формулирует уточняющие вопросы.

## 9. Поведение при больших артефактах

Если в plan встречается шаг, потенциально требующий большого ввода в контекст:

- Policy Engine применяет `large_artifact_not_sent_to_llm`;
- модель получает отказ с required `profile/index/slicing` action;
- proposal перестраивается в pipeline через детерминированный pre-processing.

## 10. Поведение при отсутствии capability

Если planner запросил capability, которой нет в реестре:

- runtime может предложить `tool_proposal` (через Tool Forge, см. [09-tool-forge.md](09-tool-forge.md));
- модель формулирует требования к tool: capability, input/output schema, риск, sandbox profile, verifier;
- регистрация невозможна без прохождения карантина.

## 11. Прозрачность и отчётность

Каждый proposal сопровождается `reason`. Каждый отказ — `reason_code`. Финальный отчёт строится не моделью «из головы», а через Report Builder, который собирает:

- цель;
- timeline шагов;
- использованные artifacts;
- verified facts (с evidence\_refs);
- неподтверждённые предположения;
- риски;
- что было откатано;
- сохранённые в память кейсы.

Модель пишет нарративную часть, но всё «техническое» Reports Builder подставляет автоматически. Это исключает выдуманные цифры и пути.

## 12. Чего модель не должна «знать»

- мастер-ключ шифрования;
- секреты в открытом виде;
- внутреннюю реализацию Kernel;
- абсолютные пути к секретам и хранилищу;
- содержимое скоупов, помеченных `sensitive`;
- содержимое целиком больших артефактов.

Всё это идёт через handles или через tools, которые применяют policy перед раскрытием.

## 13. Резюме

```
Модель умная — приятный бонус.
Модель послушная — не цель.
Цель — среда, в которой даже небольшая модель ведёт себя как инженер.
```

Главные ограничения LLM — структурные, а не риторические. Это и есть «мотивация и ограничения главного ИИ»: ему просто **не дают** вариантов сделать что-то опасное.
