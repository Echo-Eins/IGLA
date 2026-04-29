# 09. Tool Forge: безопасное саморасширение

## 1. Зачем

ИГЛА регулярно будет встречать задачи, для которых готового модуля нет. Наивный путь — позволить модели «написать tool и зарегистрировать его» — недопустим. Даже большая LLM с высокой долей вероятности выдаст модуль с дырами в безопасности, скрытыми побочными эффектами или несовместимыми schema.

Tool Forge — это **карантинный конвейер**, через который проходит любой AI-сгенерированный модуль до того, как он попадёт в Tool Registry.

## 2. Главный принцип

```
LLM = junior developer, который пишет proposal.
Runtime / static analysis / tests / security review = senior engineer.
```

Модель не «добавила себе способность». Она **предложила кандидата**, прошла CI, и только потом Kernel зарегистрировал tool.

## 3. Pipeline целиком

```
1. Tool Need Detection
   Planner: «мне не хватает инструмента X».

2. Tool Proposal
   Назначение, input/output schema, capabilities, риски, sandbox profile, verifier.

3. Human/Policy Approval (gate 1)
   Можно ли создавать такой tool в принципе?

4. Generate Code in Quarantine
   Код кладётся в `.igla/quarantine/`, не в `tools/`.

5. Generate Tests
   Unit, property-based (если применимо), schema tests.

6. Static Checks
   Lint, type check, forbidden imports, dangerous calls.

7. Sandbox Run
   Запуск на тестовых данных в изолированной среде.

8. Compatibility Check
   Manifest, schemas, versioning, capabilities.

9. Security Review
   Поиск shell/network/secrets/path-escape.

10. Human/Policy Approval (gate 2)
    Можно ли регистрировать после прохождения CI?

11. Promotion
    Tool попадает в registry, становится доступен Planner’у.
```

Без gate 1 и gate 2 регистрации нет.

## 4. Tool Proposal

```yaml
proposal_id: tp_01HX...
proposed_by: planner
reason: >
  Need to validate Quickemu .conf files against current OVMF paths
  and disk image existence.

target_tool:
  name: validate_quickemu_config
  namespace: virtualization.quickemu
  version: 0.1.0

capabilities:
  - quickemu.config.validate

input_schema_sketch:
  type: object
  required: [config_artifact_id]
  properties:
    config_artifact_id: {type: string}

output_schema_sketch:
  type: object
  properties:
    issues: {type: array, items: {type: object}}
    warnings: {type: array, items: {type: object}}
    summary: {type: string}

artifact_io:
  consumes: [QuickemuConfig]
  produces: [QuickemuConfigReport]

risk:
  level: read_only
  side_effects: false
  network: disabled

sandbox: read_only_file_access
verifier:
  type: schema_validation
```

## 5. Quarantine layout

```
.igla/quarantine/
└── tp_01HX.../
    ├── manifest.yaml         # см. 04-tool-protocol.md
    ├── tool.py               # сгенерированный код
    ├── tests/
    │   ├── test_basic.py
    │   ├── test_schema.py
    │   └── fixtures/
    ├── schemas/
    │   ├── input.v1.json
    │   └── output.v1.json
    ├── reports/
    │   ├── lint.json
    │   ├── typecheck.json
    │   ├── security.json
    │   └── sandbox_run.json
    └── status.json
```

Quarantine — отдельная директория. Tool registry даже не видит этот код, пока он не promoted.

## 6. Static Checks (минимум)

- Линтер (`ruff` / эквивалент) на стиль.
- Type checker (`mypy --strict` или эквивалент).
- AST-сканер запрещённых API:
  ```
  subprocess
  os.system / os.popen
  shell=True
  socket / asyncio open_connection
  http.client / urllib.request
  requests / httpx / aiohttp
  pathlib accesses outside allowed_paths
  open() to /etc, /root, /proc, /sys
  chmod / chown / setuid
  docker socket / kubectl
  importlib.* dynamic loading
  exec / eval
  ```
- Проверка отсутствия побочных эффектов на import (никаких top-level network/FS calls).
- Проверка манифеста на консистентность (capabilities, schemas, sandbox profile, verifier).

Любая «красная» проверка — отказ.

## 7. Sandbox Run

Сгенерированный tool запускается в изолированной среде на синтетических/реальных тестовых артефактах:

- профиль `read_only_file_access` для pure tools;
- никакой сети;
- ресурсные лимиты, как в манифесте, но более жёсткие (×0.5 от заявленных);
- собирается timing, peak memory, exit status.

Если tool «висит», падает по таймауту, аварийно завершается, нарушает лимиты — отказ.

## 8. Tests

Минимум на старте:

- `test_basic.py` — сценарий happy path с зафиксированным артефактом.
- `test_schema.py` — проверка соответствия input/output schemas.
- `test_failure.py` — поведение при невалидном input (должен возвращать корректный `ToolError`, а не падать).

Property-based testing рекомендуется для парсеров/валидаторов.

## 9. Security Review

Структурная проверка:

- нет ли «обходных путей» через символьные ссылки;
- не пытается ли tool читать вне `allowed_paths` через произвольные имена;
- нет ли скрытого кода в комментариях/строках, которые могут быть `eval`’нуты;
- не зашиты ли credentials/API endpoints;
- честно ли заявлены `risk_level` и `side_effects`.

Часть проверок — автоматическая (regex, AST). Часть — отчёт человеку для approval gate 2.

## 10. Compatibility Check

- `protocol_version` совместим с runtime;
- объявленные `consumes`/`produces` существуют в реестре артефактов либо описаны в proposal;
- input/output schema валидны и не конфликтуют с существующими;
- `version` корректно проставлен (`0.x` для quarantine; стабильная версия только после promotion).

## 11. Promotion

После approval gate 2:

- код переносится из quarantine в `tools/`;
- манифест регистрируется в `ToolRegistry`;
- запись в Memory типа `tool_birth`:
  ```json
  {
    "type": "tool",
    "scope": "global",
    "title": "validate_quickemu_config promoted",
    "source_events": ["evt_proposal_...", "evt_tests_passed_...", "evt_approval_..."]
  }
  ```
- EventStore получает `tool_promoted` событие.

## 12. Effect tools — особый режим

Effect tools (мутирующие мир) **не могут** рождаться через AI generation на ранних стадиях. Только pure tools.

Когда ИГЛЕ нужен новый effect tool:

- предложение формируется как **расширение существующего kernel effect tool** (новая опция/режим), а не как новый код;
- ядро добавляет это вручную (или через очень жёсткий PR-флоу человека).

## 13. Версионирование сгенерированных tools

- В quarantine — всегда `0.x.y` (pre-stable).
- Promotion → версия выставляется как `1.0.0`.
- Любое последующее изменение проходит тот же конвейер.
- Старые версии остаются доступны, пока на них есть зависимости.

## 14. Использование готовых рецептов

Tool Forge сначала пытается **переиспользовать** существующее:

- ищет рецепт в Memory (`50_Recipes/` в Obsidian / cases);
- ищет похожий tool по capability;
- ищет адаптер артефактов;
- только при отсутствии — генерирует новый.

Это снижает энтропию реестра и количество «почти одинаковых» tools.

## 15. Запреты Tool Forge

- AI-generated tool не может быть Effect tool (на старте).
- AI-generated tool не может писать в Memory напрямую — только через kernel `write_memory`.
- AI-generated tool не может делать сетевые вызовы — только через `network_request_proxy`.
- AI-generated tool не может вызывать другие tools (никаких «мини-оркестраторов внутри tool»).
- AI-generated tool не может обращаться к runtime state, реестру, secrets store.

## 16. Резюме

Самогенерация модулей — мощная фича, но её безопасность держится не на «модель умная», а на **карантине**. Tool Forge превращает «модель пишет код» в «модель присылает proposal, который CI принимает или отвергает», и это единственно ответственный способ масштабировать набор capability ИГЛЫ.
