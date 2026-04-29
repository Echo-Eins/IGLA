# IGLA
А можно ли на практике реализовать в коде универсальный алгоритм для решения широкого, но ограниченного круга задач? На основе готовых модулей и их сразивания системой ИИ?

Да, можно. Но это будет не «универсальный алгоритм, который сам всё решает», а **ограниченный универсальный решатель задач**: система, где ИИ выступает как **планировщик/диспетчер**, а реальные действия выполняют готовые модули.

То, что ты описываешь как «сращивание модулей системой ИИ», на практике называется примерно так:

**LLM-agent / tool-using agent / AI-orchestrator / neuro-symbolic system / workflow agent.**

Идея рабочая.

---

## Главное: ИИ не должен быть самим решателем

Лучший вариант такой:

```text
Задача пользователя
        ↓
ИИ понимает тип задачи
        ↓
ИИ строит план
        ↓
выбирает готовые модули
        ↓
запускает их по очереди
        ↓
проверяет результат
        ↓
если ошибка — перепланирует
        ↓
выдает ответ
```

То есть ИИ — это не «магическая функция solve_anything()», а **мозг-координатор**.

А модули — это обычный код:

```text
solve_math()
run_python()
search_docs()
parse_pdf()
analyze_code()
compile_project()
run_tests()
generate_image()
query_database()
call_api()
```

ИИ решает: **какой модуль вызвать, с какими параметрами, в каком порядке, и что делать с результатом**.

---

## Это реально уже используется

Например, современные API поддерживают **function calling / tool calling**, где модель не просто пишет текст, а выбирает функцию и передает ей структурированные аргументы. OpenAI описывает function calling как способ связать модель с функциональностью приложения, а Structured Outputs позволяют заставить модель возвращать данные строго по JSON-схеме. ([OpenAI Разработчики][1])

Есть и готовые фреймворки для такой архитектуры. Например, LangGraph разделяет **workflows** с заранее заданным порядком шагов и **agents**, где модель сама динамически выбирает процесс и инструменты. ([Документы LangChain][2]) Semantic Kernel описывает plugins как группы функций, которые можно отдавать AI-приложению для оркестрации. ([Microsoft Learn][3]) AutoGen и Microsoft Agent Framework идут в сторону одно- и многоагентных систем с инструментами, состоянием и workflow-логикой. ([Microsoft на GitHub][4])

---

## Что значит «широкий, но ограниченный круг задач»

Это ключевой момент.

Реализуемо не так:

```text
Реши вообще любую задачу в мире.
```

А так:

```text
Реши широкий набор задач внутри заранее заданной среды.
```

Например:

### Хорошо реализуемо

```text
1. Решение школьных задач по математике/информатике.
2. Анализ кода и автоматическое исправление ошибок.
3. Работа с файлами: PDF, TXT, DOCX, таблицы.
4. Автоматизация Linux/контейнеров/VM.
5. Генерация отчетов.
6. Поиск информации, сравнение источников.
7. Запуск тестов, сборка проекта, анализ логов.
8. Создание пайплайнов для ML/LLM/изображений.
```

### Плохо реализуемо

```text
1. Абсолютно любые задачи без ограничений.
2. Задачи без проверяемого результата.
3. Задачи, где нельзя безопасно запускать действия.
4. Задачи, где ошибка критична, но нет верификатора.
5. Полностью автономная система без контроля.
```

То есть нужен **контур применимости**.

---

## Практическая архитектура

Я бы строил так:

```text
┌────────────────────┐
│ User Request        │
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Task Normalizer     │ ← превращает запрос в структурированное ТЗ
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Planner / LLM       │ ← строит план
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Tool Registry       │ ← список доступных модулей
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Executor            │ ← запускает модули
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Verifier            │ ← проверяет результат
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Repair Loop         │ ← если ошибка, пересобрать план
└─────────┬──────────┘
          ↓
┌────────────────────┐
│ Final Answer        │
└────────────────────┘
```

Самое важное здесь — **Tool Registry** и **Verifier**.

---

## Как должен выглядеть модуль

Каждый модуль должен быть не просто функцией, а описанной способностью:

```python
{
    "name": "run_python",
    "description": "Executes Python code in an isolated sandbox",
    "input_schema": {
        "code": "string",
        "timeout": "integer"
    },
    "output_schema": {
        "stdout": "string",
        "stderr": "string",
        "exit_code": "integer"
    },
    "side_effects": false,
    "requires_approval": false
}
```

То есть ИИ должен понимать:

```text
что модуль умеет;
какие аргументы принимает;
что возвращает;
опасен ли модуль;
можно ли его запускать автоматически;
как проверить результат.
```

---

## Минимальный псевдокод

Упрощенно:

```python
class Tool:
    def __init__(self, name, description, input_schema, func):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.func = func


class Orchestrator:
    def __init__(self, llm, tools, verifier):
        self.llm = llm
        self.tools = {tool.name: tool for tool in tools}
        self.verifier = verifier

    def solve(self, user_task: str):
        task_spec = self.normalize_task(user_task)

        for attempt in range(5):
            plan = self.plan(task_spec)

            context = {}
            for step in plan["steps"]:
                tool_name = step["tool"]
                args = step["args"]

                tool = self.tools[tool_name]
                result = tool.func(**args)

                context[step["id"]] = result

            verdict = self.verifier.check(task_spec, context)

            if verdict["ok"]:
                return self.finalize(task_spec, context)

            task_spec["previous_error"] = verdict["reason"]

        raise RuntimeError("Could not solve task reliably")
```

Но в боевой системе этого мало. Нужны еще:

```text
логирование;
изоляция кода;
ограничения прав;
кэширование;
память;
система тестов;
контроль стоимости;
ручное подтверждение опасных действий;
разделение планировщика и исполнителя;
валидация JSON;
защита от prompt injection.
```

---

## Где ИИ реально полезен

ИИ хорошо делает:

```text
1. Понимает кривой человеческий запрос.
2. Разбивает задачу на шаги.
3. Выбирает нужные инструменты.
4. Объясняет результат.
5. Переходит на другой план, если первый не сработал.
6. Связывает несовместимые на первый взгляд модули.
```

Например, пользователь пишет:

```text
"У меня vLLM не стартует в контейнере, вот лог"
```

ИИ может построить цепочку:

```text
parse_log()
↓
detect_error()
↓
search_known_issues()
↓
inspect_container_config()
↓
suggest_patch()
↓
generate_dockerfile_fix()
↓
run_validation()
```

Но саму проверку должен делать не «на глаз», а через реальные модули: парсер логов, grep, запуск команды, тест, статический анализ.

---

## Где ИИ лучше не использовать как единственный механизм

ИИ плохо использовать как единственный источник истины там, где нужна точность:

```text
математика;
юридические сроки;
деньги;
системные команды;
миграции базы данных;
удаление файлов;
запуск shell-команд;
изменение конфигов;
деплой.
```

Там ИИ должен только **предлагать план**, а проверка должна быть машинной.

Например:

```text
ИИ: "думаю, надо изменить nftables правило"
Код: проверяет синтаксис nft -c
Код: делает backup
Код: применяет правило
Код: проверяет соединение
Код: откатывает, если сломалось
```

---

## Очень важная идея: не «один агент», а несколько ролей

Для более надежной системы можно разделить роли:

```text
Planner       — строит план.
Executor      — запускает инструменты.
Critic        — ищет ошибки.
Verifier      — проверяет результат.
Memory        — хранит состояние.
Policy Guard  — запрещает опасные действия.
```

Это уже похоже на маленькую ОС для задач.

Например:

```text
Пользователь: "почини проект"
Planner: "нужно запустить тесты, найти ошибку, исправить"
Executor: запускает pytest
Critic: анализирует traceback
Executor: патчит файл
Verifier: снова запускает pytest
Finalizer: объясняет, что изменено
```

---

## Какой стек можно использовать

Для самостоятельной реализации:

```text
Python
Pydantic
FastAPI
SQLite/PostgreSQL
Docker sandbox
Celery/RQ/Arq для очередей
LLM API или локальная модель
JSON Schema
pytest для проверки
```

Для агентной части можно смотреть:

```text
LangGraph — если хочешь явно задавать графы, циклы, состояния.
Semantic Kernel / Microsoft Agent Framework — если хочешь plugin/function orchestration.
AutoGen-подобный подход — если хочешь несколько агентов, которые общаются.
Свой минимальный orchestrator — если нужна максимальная прозрачность.
```

Я бы для твоего стиля задач выбрал **свой orchestrator + Pydantic + Docker sandbox**, а не начинал сразу с тяжелого агентного фреймворка.

---

## Как это могло бы называться

По сути, ты описываешь:

```text
модульную самоорганизующуюся систему решения задач
```

или более инженерно:

```text
AI Orchestrated Modular Solver
```

или:

```text
Bounded General Task Solver
```

или:

```text
Tool-Augmented Agent Runtime
```

---

## Главная опасность

Самая большая ошибка — дать ИИ слишком много свободы.

Плохо:

```text
ИИ может сам писать код, сам запускать shell, сам удалять файлы, сам менять систему.
```

Хорошо:

```text
ИИ предлагает действие.
Система проверяет JSON.
Policy Guard проверяет безопасность.
Executor запускает в sandbox.
Verifier проверяет результат.
Только потом действие считается успешным.
```

---

## Вывод

**Да, такую систему можно реализовать.** Более того, это один из самых практичных путей к «псевдоуниверсальному» ИИ-решателю.

Но правильная формула такая:

```text
Не универсальный алгоритм,
а универсальный оркестратор внутри ограниченной среды.
```

ИИ должен не заменять все модули, а **собирать из них рабочие цепочки**:

```text
понять задачу → выбрать инструменты → выполнить → проверить → исправить → объяснить
```

И чем лучше описаны модули, схемы входов/выходов, ограничения и верификаторы, тем ближе система будет к настоящему «универсальному решателю» для выбранного класса задач.

[1]: https://developers.openai.com/api/docs/guides/structured-outputs?utm_source=chatgpt.com "Structured model outputs | OpenAI API"
[2]: https://docs.langchain.com/oss/python/langgraph/workflows-agents?utm_source=chatgpt.com "Workflows and agents - Docs by LangChain"
[3]: https://learn.microsoft.com/en-us/semantic-kernel/concepts/plugins/?utm_source=chatgpt.com "Plugins in Semantic Kernel"
[4]: https://microsoft.github.io/autogen/stable//index.html?utm_source=chatgpt.com "AutoGen"


Я бы **начинал на Python**, но архитектуру сразу делал так, чтобы потом можно было выносить отдельные модули в **Rust**.

То есть не:

```text
выбрать Python или Rust навсегда
```

а:

```text
Python = мозг / оркестратор / AI-логика
Rust   = быстрые и безопасные исполнительные модули
```

## Мой выбор для твоей идеи

Для системы типа:

```text
ИИ понял задачу → выбрал модули → связал их → запустил → проверил результат
```

лучше стартовать с **Python**.

Причины:

1. **LLM/AI-экосистема в Python намного удобнее.**
   Большинство агентных фреймворков, валидации структурированных ответов, tool-calling, ML-интеграций, векторных баз, парсеров документов и API-клиентов сначала нормально живут именно в Python. Например, LangGraph прямо разделяет предсказуемые workflows и динамических agents, а также дает инфраструктуру для stateful workflow/agent-сценариев. ([docs.langchain.com][1])

2. **Для схем входов/выходов Python очень удобен.**
   Pydantic хорошо подходит для описания инструментов, валидации аргументов, JSON-схем, ответов модулей и структурированного состояния оркестратора. Его документация прямо позиционирует библиотеку как validation layer на основе Python type annotations. ([pydantic.dev][2])

3. **Быстрее собрать прототип.**
   Для такой системы на старте важнее не максимальная скорость, а быстро проверить архитектуру: как агент планирует, как выбирает инструменты, как валидирует результат, как делает retry.

4. **Python легче склеивает всё подряд.**
   Docker, shell, файлы, PDF, таблицы, модели, web API, базы, vLLM, PyTorch, transformers, FastAPI — всё это быстрее собрать на Python.

---

## Где Rust реально лучше

Rust я бы использовал не вместо Python, а **под Python** — для отдельных компонентов:

```text
1. sandbox runner;
2. безопасный executor команд;
3. быстрый парсер логов;
4. file watcher;
5. планировщик задач;
6. сетевой gateway;
7. модуль изоляции прав;
8. runtime для долгоживущих процессов;
9. критичные security-компоненты;
10. производительные пайплайны обработки данных.
```

Rust особенно хорош там, где важны:

```text
предсказуемость;
безопасность памяти;
строгая типизация;
отсутствие "магии";
быстрый CLI;
низкоуровневый контроль;
надёжная многопоточность;
сетевые сервисы.
```

Для асинхронных сетевых сервисов Rust-экосистема зрелая: Tokio описывается как async runtime для надежных приложений, с I/O, networking, scheduling, timers и прочими базовыми блоками. ([tokio.rs][3])

---

## Лучший вариант: Python-first, Rust-ready

Я бы делал так:

```text
ai_solver/
├── orchestrator/          # Python
│   ├── planner.py
│   ├── agent_loop.py
│   ├── tool_registry.py
│   ├── verifier.py
│   └── memory.py
│
├── tools/                 # Python-модули
│   ├── run_python.py
│   ├── parse_pdf.py
│   ├── search_files.py
│   ├── run_tests.py
│   └── docker_exec.py
│
├── schemas/               # Pydantic-схемы
│   ├── task.py
│   ├── plan.py
│   ├── tool_call.py
│   └── result.py
│
├── rust_core/             # Rust workspace
│   ├── sandbox_runner/
│   ├── log_parser/
│   ├── policy_guard/
│   └── process_supervisor/
│
├── api/                   # FastAPI
│   └── server.py
│
└── storage/
    └── sqlite / postgres
```

Rust можно подключать к Python через **PyO3**. Он позволяет писать нативные Python-модули на Rust или встраивать Python в Rust-бинарник. ([pyo3.rs][4])

То есть сначала делаешь Python-функцию:

```python
def parse_huge_log(path: str) -> dict:
    ...
```

А потом, когда она становится медленной или критичной, заменяешь её Rust-модулем:

```python
from rust_log_parser import parse_huge_log
```

Для Python-кода почти ничего не меняется.

---

## Когда выбрать только Python

Выбирай **чистый Python**, если первая цель — быстро получить рабочую систему:

```text
ИИ-агент;
tool registry;
планировщик;
валидация JSON;
работа с файлами;
web API;
интеграция с LLM;
работа с Docker;
простая память;
логика retry;
первые плагины.
```

Для MVP я бы вообще не трогал Rust.

Сначала надо доказать, что сама идея работает:

```text
задача → план → вызов инструментов → проверка → исправление → финальный ответ
```

А уже потом оптимизировать.

---

## Когда выбрать только Rust

Только Rust я бы выбрал, если ты делаешь не AI-оркестратор, а скорее:

```text
локальный daemon;
системный task runner;
безопасный executor;
замену make/celery/systemd-run;
низкоуровневую платформу для запуска задач;
агент, который должен жить месяцами без падений.
```

Но для AI-части Rust будет заметно менее удобен. Придётся больше писать руками: схемы, обвязки API, обработку tool-calling, интеграции с моделями, плагины, парсеры, веб-интерфейс. Это возможно, но скорость разработки будет ниже.

---

## Мой практический совет

Я бы сделал так:

### Этап 1 — Python MVP

```text
Python + FastAPI + Pydantic + SQLite/Postgres + Docker sandbox
```

Минимальная система:

```text
1. принимает задачу;
2. приводит её к TaskSpec;
3. просит LLM построить план;
4. валидирует план через Pydantic;
5. вызывает разрешённые tools;
6. проверяет результат;
7. делает retry;
8. пишет полный лог выполнения.
```

### Этап 2 — строгие контракты

Каждый tool описать схемой:

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    dangerous: bool = False
    requires_approval: bool = False
```

Тут Python/Pydantic прям идеально ложится.

### Этап 3 — Rust для ядра безопасности

В Rust вынести:

```text
policy_guard;
process_runner;
filesystem sandbox;
network restrictions;
timeout/kill logic;
resource limits;
log streaming.
```

### Этап 4 — Rust-модули как плагины

Тяжелые модули писать на Rust, но подключать к Python через PyO3.

---

## Итог

Для твоей идеи я бы выбрал:

```text
80% Python
20% Rust
```

Причём порядок такой:

```text
сначала Python,
потом Rust там, где Python реально начал мешать.
```

Главная архитектурная формула:

```text
Python — думает, планирует, связывает.
Rust — быстро и безопасно исполняет.
```

Если писать всё на Rust с нуля, можно получить красивую и надежную систему, но очень медленно. Если писать всё на Python, можно быстро получить рабочий прототип, но потом упереться в безопасность, типовую строгость и надежность executor-части.

Самый боевой вариант — **Python-оркестратор + Rust-core для исполнения и sandbox/security**.

[1]: https://docs.langchain.com/oss/python/langgraph/workflows-agents?utm_source=chatgpt.com "Workflows and agents - Docs by LangChain"
[2]: https://pydantic.dev/docs/validation/latest/get-started/?utm_source=chatgpt.com "Welcome to Pydantic | Pydantic Docs"
[3]: https://tokio.rs/?utm_source=chatgpt.com "Tokio - An asynchronous Rust runtime"
[4]: https://pyo3.rs/?utm_source=chatgpt.com "Introduction - PyO3 user guide"


Окей, что мы реально можем заставить ИГЛУ делать? Помимо поиска, чтения, создания документов, открытия приложений, поиска в интернете или еще чего... как сделать это архитектурно так, чтобы ИГЛА стала РЕАЛЬНЫМ отражением своего названия?

Да. Чтобы **ИГЛА** стала реальным отражением названия, её надо проектировать не как «чат-бота, который умеет пользоваться инструментами», а как **локальную итеративную систему анализа, прогнозирования, экспериментов и саморасширения**.

То есть:

```text
И — Итеративно:
    не один ответ, а цикл: наблюдение → гипотеза → действие → проверка → коррекция.

Г — Генерируемый:
    система сама генерирует планы, проверки, скрипты, тесты, патчи, пайплайны.

Л — Локальный:
    анализ основан на реальном состоянии твоей машины, контейнеров, файлов, логов, проектов.

А — Анализ:
    не просто "сделал", а построил модель ситуации, проверил гипотезы и доказал результат.
```

Коротко: **ИГЛА должна быть не поисковиком, а локальным инженерным мозгом с руками и лабораторией.**

---

# Что ИГЛА реально может делать

Помимо поиска, чтения, создания документов и запуска приложений, её можно заставить делать вот это.

## 1. Диагностировать систему как инженер

Например, ты пишешь:

```text
ИГЛА, почему vLLM не стартует в Workbench?
```

Она должна не просто ответить из головы, а сделать цепочку:

```text
1. Найти контейнер.
2. Прочитать логи.
3. Определить команду запуска.
4. Проверить версию vLLM.
5. Проверить аргументы CLI.
6. Проверить CUDA, torch, GPU, VRAM.
7. Сравнить с известными возможностями.
8. Сформировать гипотезы.
9. Предложить патч.
10. Запустить тестовую команду в sandbox.
11. Проверить, поднялся ли порт.
12. Сохранить рецепт решения.
```

Для твоих задач это особенно мощно:

```text
Gentoo
Quickemu
QEMU/OVMF
nftables killswitch
Docker
NVIDIA Workbench
vLLM
llama.cpp
PyTorch/CUDA
локальные модели
```

ИГЛА могла бы быть твоим **локальным SRE/DevOps/ML-инженером**, который не гадает, а проверяет.

---

## 2. Строить гипотезы и проверять их экспериментами

Вот это самое важное.

Обычный ассистент:

```text
"Возможно, проблема в версии библиотеки."
```

ИГЛА:

```text
Гипотеза 1: аргумент --enable-ui не поддерживается текущей версией vLLM.
Проверка: vllm serve --help | grep enable-ui
Результат: аргумент отсутствует.
Вывод: гипотеза подтверждена.

Гипотеза 2: проблема в CUDA.
Проверка: python -c 'import torch; print(torch.cuda.is_available())'
Результат: CUDA доступна.
Вывод: гипотеза отклонена.
```

То есть ИГЛА должна иметь не просто planner, а **hypothesis engine**.

Архитектурно:

```text
Problem
  ↓
Observation
  ↓
Hypothesis Set
  ↓
Experiment Plan
  ↓
Execution
  ↓
Evidence
  ↓
Verdict
  ↓
Next Iteration
```

Это уже похоже на настоящее значение «итеративно-генерируемый локальный анализ».

---

## 3. Самостоятельно создавать временные инструменты

Например, у ИГЛЫ нет готового модуля для анализа логов vLLM.

Тогда она может:

```text
1. Сгенерировать маленький Python/Rust-скрипт.
2. Запустить его в sandbox.
3. Проверить результат.
4. Если полезно — предложить добавить как постоянный модуль.
```

Например:

```text
parse_vllm_log.py
detect_qemu_audio_errors.py
analyze_nft_rules.py
inspect_cuda_stack.py
quickemu_config_validator.py
```

Но важно: **не сразу добавлять в боевую систему**, а через quarantine.

```text
Generated Tool
  ↓
Sandbox Test
  ↓
Static Analysis
  ↓
Unit Tests
  ↓
Human Approval
  ↓
Tool Registry
```

Так ИГЛА начинает **расширять собственный набор навыков**, но безопасно.

---

## 4. Строить локальную модель твоей машины

Не просто каждый раз заново смотреть файлы, а иметь внутреннюю карту:

```text
Host:
  OS: Gentoo
  GPU: RTX 3080 / GB10 Spark
  VM stack: Quickemu + QEMU
  Firewall: nftables
  Containers: Docker / Workbench
  AI stack: vLLM / llama.cpp / PyTorch
  Important paths:
    ~/VM/quickemu
    ~/Coding/C/llama.cpp
    /workspace/logs/vllm/server.log
```

Но это не должна быть «память в стиле заметок». Это должна быть **локальная knowledge graph / state graph**.

Пример:

```text
vLLM container
  uses image → ghcr.io/echo-eins/...
  serves model → openai/gpt-oss-20b
  port → 8000
  log path → /workspace/logs/vllm/server.log
  failure → unrecognized --enable-ui
  fix → remove --enable-ui
```

Тогда ИГЛА может рассуждать не в пустоте, а по локальной карте.

---

## 5. Делать причинно-следственный анализ

Не просто:

```text
"Ошибка в логах."
```

А:

```text
Событие:
  vLLm не стартует.

Прямой симптом:
  unrecognized arguments: --enable-ui

Причина:
  текущая версия CLI не поддерживает этот флаг.

Последствие:
  процесс завершается до запуска сервера.

Исправление:
  убрать флаг или заменить на поддерживаемый способ запуска UI.

Проверка:
  vllm serve --help
  curl http://127.0.0.1:8000/v1/models
```

Это важная часть «А» — анализа.

---

## 6. Управлять проектами как автономный workflow-engine

ИГЛА может не только отвечать, но и вести задачу как workflow:

```text
Цель:
  Поднять локальный vLLM-сервер с моделью X.

План:
  1. Проверить GPU.
  2. Проверить драйвер.
  3. Проверить CUDA.
  4. Проверить наличие модели.
  5. Проверить формат весов.
  6. Сформировать команду запуска.
  7. Запустить сервер.
  8. Проверить endpoint.
  9. Сделать systemd/docker-compose/workbench config.
  10. Сохранить инструкцию.
```

То есть ИГЛА становится не просто «помощником», а **машиной выполнения инженерных задач**.

---

# Главная архитектура

Я бы строил ИГЛУ так:

```text
┌────────────────────────────────────┐
│              UI / API              │
│  Web GUI, CLI, Telegram, local app  │
└─────────────────┬──────────────────┘
                  ↓
┌────────────────────────────────────┐
│          Task Understanding         │
│  превращает запрос в TaskSpec       │
└─────────────────┬──────────────────┘
                  ↓
┌────────────────────────────────────┐
│          IGLA Core Loop             │
│ observe → hypothesize → plan → act  │
│ verify → learn → repeat             │
└─────────────────┬──────────────────┘
                  ↓
┌────────────────────────────────────┐
│            Tool Registry            │
│ shell, docker, files, git, tests,   │
│ browser, pdf, code, VM, GPU, etc.   │
└─────────────────┬──────────────────┘
                  ↓
┌────────────────────────────────────┐
│          Execution Sandbox          │
│ Docker, firejail, nsjail, bubblewrap│
└─────────────────┬──────────────────┘
                  ↓
┌────────────────────────────────────┐
│       Verifier / Evidence Engine    │
│ тесты, логи, exit codes, checks     │
└─────────────────┬──────────────────┘
                  ↓
┌────────────────────────────────────┐
│          Memory / State Graph       │
│ факты, рецепты, прошлые решения     │
└────────────────────────────────────┘
```

---

# Самое важное ядро: IGLA Loop

Настоящая ИГЛА должна работать не так:

```text
User → LLM → Answer
```

А так:

```text
User
 ↓
TaskSpec
 ↓
Observe local state
 ↓
Generate hypotheses
 ↓
Rank hypotheses
 ↓
Generate experiments
 ↓
Run safe experiments
 ↓
Collect evidence
 ↓
Update state
 ↓
Generate action plan
 ↓
Execute reversible steps
 ↓
Verify
 ↓
If failed: new iteration
 ↓
Final answer + proof
```

В псевдокоде:

```python
def igla_solve(task: UserTask) -> FinalReport:
    state = observe_environment(task)
    memory = load_relevant_memory(task, state)

    for iteration in range(MAX_ITERATIONS):
        hypotheses = generate_hypotheses(task, state, memory)
        experiments = design_experiments(task, state, hypotheses)

        evidence = []
        for experiment in experiments:
            if policy_guard.allows(experiment):
                result = sandbox.run(experiment)
                evidence.append(result)

        analysis = analyze_evidence(task, hypotheses, evidence)

        if analysis.confidence >= REQUIRED_CONFIDENCE:
            plan = generate_action_plan(task, analysis)

            safe_plan = policy_guard.filter(plan)
            execution_result = executor.run_with_rollback(safe_plan)

            verdict = verifier.verify(task, execution_result)

            if verdict.ok:
                memory.save_case(task, analysis, execution_result, verdict)
                return final_report(task, analysis, execution_result, verdict)

        state = update_state(state, evidence, analysis)

    return partial_report(task, state, evidence)
```

Вот это уже **ИГЛА**, а не просто агент.

---

# Компоненты, которые нужны

## 1. TaskSpec

Любой запрос должен превращаться в структурированный объект.

Например:

```json
{
  "goal": "Fix vLLM startup in NVIDIA Workbench container",
  "domain": "ml_infra",
  "risk": "medium",
  "requires_local_access": true,
  "success_criteria": [
    "vLLM process starts",
    "HTTP endpoint responds",
    "model appears in /v1/models"
  ],
  "allowed_actions": [
    "read_logs",
    "run_diagnostic_commands",
    "edit_project_files",
    "restart_container_after_approval"
  ],
  "forbidden_actions": [
    "delete_models",
    "upload_secrets",
    "change_firewall_without_approval"
  ]
}
```

Это защищает от хаоса.

---

## 2. Tool Registry

Все возможности ИГЛЫ должны быть описаны как инструменты.

Не просто функция:

```python
run_shell(command)
```

А полноценный контракт:

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    risk_level: str
    side_effects: bool
    requires_approval: bool
    verifier: str | None
```

Пример:

```json
{
  "name": "docker_exec",
  "description": "Run a command inside a Docker container",
  "risk_level": "medium",
  "side_effects": true,
  "requires_approval": true,
  "input_schema": {
    "container": "string",
    "command": "string",
    "timeout": "integer"
  },
  "output_schema": {
    "stdout": "string",
    "stderr": "string",
    "exit_code": "integer"
  }
}
```

Главная идея: **ИИ не должен иметь прямую магическую власть.**
Он должен просить executor выполнить строго описанный tool call.

---

## 3. Policy Guard

Это обязательный слой.

Он решает:

```text
Можно ли это действие?
Нужен ли approval?
Есть ли backup?
Есть ли rollback?
Не утекают ли секреты?
Не удаляются ли файлы?
Не меняется ли сеть?
Не лезем ли в root без причины?
```

Пример уровней:

```text
Level 0 — только чтение.
Level 1 — создание новых файлов.
Level 2 — изменение файлов с backup.
Level 3 — запуск команд.
Level 4 — изменение сервисов/контейнеров.
Level 5 — firewall, root, сеть, удаление, credentials.
```

Автоматически можно разрешать только уровни 0–2.
Уровни 3–5 — через подтверждение или специальные политики.

---

## 4. Evidence Engine

ИГЛА должна всё доказывать.

После каждого действия она должна иметь evidence:

```text
Команда:
  vllm serve --help

Результат:
  exit_code = 0

Найдено:
  --enable-ui отсутствует

Вывод:
  текущая версия vLLM не поддерживает этот аргумент
```

Финальный ответ должен быть не:

```text
"Я думаю, проблема исправлена."
```

А:

```text
"Проверка /v1/models вернула HTTP 200, модель gpt-oss-20b видна, процесс vLLM активен."
```

Это резко повышает надежность.

---

## 5. Verifier

Каждый тип задачи должен иметь критерии успеха.

Например:

### Для кода

```text
pytest passed
mypy passed
ruff passed
program exits with 0
```

### Для vLLM

```text
process is alive
port is open
/v1/models returns expected model
test completion works
VRAM usage acceptable
```

### Для Quickemu

```text
config validates
OVMF path exists
disk image exists
QEMU starts
monitor socket appears
VM process alive
```

### Для nftables killswitch

```text
nft -c passes
ruleset contains expected chain
traffic blocked without tun0
traffic allowed with tun0
cleanup restores network
```

Без verifier ИГЛА будет просто «уверенно болтать».
С verifier она становится инженерной системой.

---

# Как сделать «генерируемость»

Название ИГЛА требует, чтобы система не только использовала готовые инструменты, но и **генерировала новые локальные процедуры**.

Это можно сделать через 4 уровня.

## Уровень 1 — генерируемые планы

```text
Система сама строит workflow под задачу.
```

Например:

```text
diagnose_vllm_startup:
  - inspect logs
  - inspect command
  - inspect vllm version
  - inspect GPU
  - test minimal startup
  - generate corrected command
```

## Уровень 2 — генерируемые проверки

```text
Система сама придумывает, как проверить успех.
```

Например:

```bash
curl -s http://127.0.0.1:8000/v1/models | jq
```

## Уровень 3 — генерируемые скрипты

```text
Система создает временные diagnostic scripts.
```

Например:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[GPU]"
nvidia-smi

echo "[Torch]"
python - <<'PY'
import torch
print(torch.__version__)
print(torch.version.cuda)
print(torch.cuda.get_device_name(0))
PY

echo "[vLLM]"
python -m vllm.entrypoints.openai.api_server --help | head -100
```

## Уровень 4 — генерируемые постоянные модули

Если временный скрипт оказался полезным, ИГЛА предлагает:

```text
"Добавить это как постоянный модуль inspect_vllm_env?"
```

После этого:

```text
generated tool → sandbox tests → review → registry
```

Так ИГЛА реально начинает эволюционировать.

---

# Что ИГЛА могла бы делать конкретно для тебя

## 1. Gentoo-системный инженер

```text
- проверять USE-флаги;
- анализировать emerge errors;
- искать конфликтующие ebuild-флаги;
- собирать план установки;
- проверять kernel config;
- валидировать dracut/initramfs;
- анализировать SDDM/KWin/Wayland/X11 проблемы;
- строить rollback-планы.
```

## 2. Quickemu/QEMU-инженер

```text
- читать *.conf VM;
- проверять OVMF пути;
- проверять диски;
- анализировать qemu logs;
- диагностировать audio/display/network;
- проверять virtio/virgl/spice/sdl;
- предлагать безопасные правки;
- запускать тестовый boot.
```

## 3. Privacy/killswitch-инженер

```text
- анализировать nftables ruleset;
- проверять tun0;
- проверять DNS через resolvectl;
- тестировать маршруты;
- симулировать "tun0 down";
- проверять, что VM не имеет интернета без VPN;
- сохранять отчет.
```

## 4. ML/LLM-инфраструктурный инженер

```text
- проверять CUDA/PyTorch;
- проверять vLLM/llama.cpp;
- проверять модельные файлы;
- строить команды запуска;
- подбирать gpu-memory-utilization;
- проверять context length;
- запускать benchmark;
- сравнивать tokens/sec;
- сохранять оптимальные профили запуска.
```

## 5. Code-repair агент

```text
- читать проект;
- строить граф зависимостей;
- запускать тесты;
- находить traceback;
- патчить код;
- снова запускать тесты;
- делать git diff;
- объяснять изменение.
```

## 6. Локальная лаборатория

Это очень важно.

ИГЛА должна уметь не только «ответить», а создать эксперимент:

```text
"Сравни llama.cpp и vLLM на этой модели"
```

Она делает:

```text
1. Проверяет окружение.
2. Формирует одинаковый prompt.
3. Запускает vLLM.
4. Измеряет tokens/sec.
5. Останавливает.
6. Запускает llama.cpp.
7. Измеряет tokens/sec.
8. Сравнивает VRAM/RAM/скорость.
9. Пишет отчет.
```

Вот это уже прям настоящая ИГЛА.

---

# Как сделать локальность

Локальность — не значит «без интернета».
Локальность значит:

```text
главный источник истины — твоя реальная среда.
```

ИГЛА должна сначала смотреть:

```text
локальные файлы;
логи;
конфиги;
версии;
команды;
состояние процессов;
состояние контейнеров;
GPU;
сеть;
порты;
git diff;
тесты.
```

А интернет использовать только как внешний источник:

```text
документация;
issue;
release notes;
StackOverflow/GitHub discussions;
спецификации;
API changes.
```

Приоритет должен быть такой:

```text
1. Локальное наблюдение.
2. Локальная проверка.
3. Официальная документация.
4. Интернет.
5. Модельная догадка.
```

То есть если ИГЛА видит в твоем логе:

```text
vllm: error: unrecognized arguments: --enable-ui
```

она не должна философствовать.
Она должна проверить локально:

```bash
vllm serve --help
```

---

# Самая мощная идея: ИГЛА как prediction engine

Чтобы она соответствовала лору, ей нужен не только repair loop, а **модуль прогнозирования**.

Например, перед действием она должна оценивать:

```text
Что произойдет, если применить это изменение?
Какие риски?
Что может сломаться?
Как откатить?
Как проверить успех?
```

Пример:

```text
Действие:
  изменить nftables ruleset.

Прогноз:
  возможная потеря интернет-соединения на host.

Риск:
  высокий.

Перед действием:
  сохранить текущий ruleset;
  проверить синтаксис nft -c;
  подготовить auto-rollback через 30 секунд;
  применить;
  проверить SSH/local connectivity;
  отменить rollback, если все хорошо.
```

Это уже очень близко к «Итеративно-Генерируемому Локальному Анализу».

---

# Архитектурный принцип: всё должно быть обратимо

ИГЛА не должна просто «делать».

Она должна делать так:

```text
before snapshot
  ↓
action
  ↓
verification
  ↓
commit or rollback
```

Для файлов:

```text
backup → edit → diff → test → keep/revert
```

Для git-проектов:

```text
branch → patch → tests → diff → commit suggestion
```

Для Docker:

```text
inspect → create test container → run → verify → replace
```

Для системы:

```text
dry-run → backup config → apply → healthcheck → rollback if failed
```

Это отличает реальную инженерную систему от опасного агента.

---

# Как должна выглядеть память ИГЛЫ

Не просто чатовая история, а несколько типов памяти.

## 1. Fact Memory

```text
На этой машине используется Gentoo.
Quickemu version: 4.9.7.
QEMU version: 10.2.0.
OVMF path: /usr/share/edk2/OvmfX64/OVMF_CODE.fd.
```

## 2. Case Memory

```text
Проблема:
  vLLM не стартовал из-за --enable-ui.

Решение:
  убрать --enable-ui.

Проверка:
  /v1/models вернул HTTP 200.
```

## 3. Tool Memory

```text
Для диагностики vLLM использовать inspect_vllm_env.
Для Quickemu использовать validate_quickemu_vm.
```

## 4. Preference Memory

```text
Пользователь предпочитает подробные шаги.
Пользователь хочет безопасные rollback-планы.
Пользователь предпочитает боевые решения без заглушек.
```

## 5. Environment State

```text
Текущие контейнеры.
Текущие VM.
Текущие модели.
Текущие сервисы.
```

---

# Как сделать саморасширение безопасным

ИГЛА может писать себе новые модули, но не должна сразу доверять собственному коду.

Правильный pipeline:

```text
1. ИГЛА понимает, что ей не хватает инструмента.
2. Генерирует Tool Proposal.
3. Создает код в quarantine.
4. Пишет тесты.
5. Запускает тесты.
6. Проверяет безопасность.
7. Показывает человеку diff.
8. После подтверждения регистрирует tool.
```

Пример:

```text
Нужен инструмент:
  validate_quickemu_config

ИГЛА генерирует:
  tools/quickemu_validator.py
  tests/test_quickemu_validator.py
  schemas/quickemu.py
```

После проверки инструмент появляется в registry:

```json
{
  "name": "validate_quickemu_config",
  "risk_level": "low",
  "side_effects": false,
  "requires_approval": false
}
```

Вот это уже будет система, которая **растит себя через задачи**.

---

# Какой должен быть MVP

Я бы не начинал сразу с «супер-ИИ».
Я бы сделал первую боевую версию так.

## MVP-1: локальный read-only анализатор

Умеет:

```text
- читать логи;
- читать конфиги;
- смотреть версии;
- смотреть процессы;
- смотреть docker containers;
- смотреть GPU;
- смотреть git status;
- строить гипотезы;
- выдавать план действий.
```

Без права менять систему.

Это безопасно и уже полезно.

---

## MVP-2: reversible executor

Добавить действия с откатом:

```text
- создавать файлы;
- править файлы с backup;
- запускать команды в sandbox;
- запускать тесты;
- делать git diff;
- откатывать изменения.
```

---

## MVP-3: domain packs

Добавить наборы навыков:

```text
igla-gentoo
igla-quickemu
igla-vllm
igla-docker
igla-nftables
igla-python-projects
igla-llama-cpp
```

Каждый pack содержит:

```text
tools;
schemas;
verifiers;
recipes;
known failure patterns;
diagnostic commands.
```

---

## MVP-4: experiment engine

Добавить способность сравнивать варианты:

```text
- запустить A;
- измерить;
- запустить B;
- измерить;
- сравнить;
- выбрать оптимальное;
- сохранить профиль.
```

---

## MVP-5: tool generation

Добавить безопасное создание новых модулей.

---

# Реальный пример поведения ИГЛЫ

Ты пишешь:

```text
ИГЛА, у меня Windows 11 VM должна ходить только через tun0.
Проверь, что killswitch реально работает.
```

Нормальная ИГЛА делает:

```text
1. Читает текущий nft ruleset.
2. Проверяет наличие tun0.
3. Проверяет IPv4 на tun0.
4. Проверяет routes.
5. Находит процесс QEMU.
6. Проверяет, через какой интерфейс идет трафик.
7. Запускает безопасный тест без изменения правил.
8. Генерирует план проверки tun0 down.
9. Предупреждает, что тест может временно оборвать сеть VM.
10. После подтверждения выключает/симулирует tun0.
11. Проверяет, что VM не имеет выхода.
12. Возвращает tun0.
13. Проверяет восстановление.
14. Пишет отчет.
```

Финальный отчет:

```text
Статус: killswitch работает.

Доказательства:
- chain inet vmks output активен;
- non-tun0 трафик получает reject;
- при tun0 down VM не получила внешний доступ;
- после восстановления tun0 доступ вернулся;
- DNS закреплен за tun0.
```

Это не «чат». Это локальный проверяющий механизм.

---

# Другой пример: ИГЛА для vLLM

Ты пишешь:

```text
ИГЛА, подними MiniMax в контейнере и найди оптимальные параметры.
```

Она делает:

```text
1. Проверяет GPU.
2. Проверяет VRAM.
3. Проверяет модель.
4. Проверяет формат.
5. Строит стартовую команду.
6. Запускает vLLM с conservative settings.
7. Проверяет endpoint.
8. Запускает benchmark.
9. Повышает gpu-memory-utilization.
10. Проверяет стабильность.
11. Подбирает max-model-len.
12. Сохраняет лучший профиль.
13. Генерирует docker-compose/start script.
```

Результат:

```text
Лучший стабильный профиль:
  gpu-memory-utilization: 0.82
  max-model-len: 65536
  kv-cache-dtype: fp8_e4m3
  average throughput: X tokens/sec

Сохранено:
  profiles/minimax-vllm-gb10.json
  scripts/start-minimax-vllm.sh
```

Вот это уже настоящая локальная ИГЛА.

---

# Какая модель ИИ нужна

Не обязательно сразу сверхмодель.

Можно разделить:

```text
Большая LLM:
  сложное планирование, анализ, объяснение.

Маленькая локальная LLM:
  классификация логов, простые решения, routing.

Детерминированный код:
  проверки, запуск команд, тесты, парсинг, rollback.

Embeddings:
  поиск по локальным документам, логам, прошлым кейсам.
```

То есть ИГЛА не должна быть одной моделью.
Она должна быть **системой вокруг модели**.

---

# Самое важное: ИГЛА должна иметь “петлю реальности”

Без этого она будет просто красиво врать.

Петля реальности:

```text
Сказала → проверила.
Предположила → провела эксперимент.
Исправила → запустила тест.
Сделала вывод → приложила evidence.
```

Формула:

```text
LLM без verifier = болтун.
LLM + tools = агент.
LLM + tools + verifier + rollback + memory = ИГЛА.
```

---

# Как я бы назвал основные подсистемы

```text
IGLA Core
  главный цикл анализа.

Observer
  собирает состояние системы.

Hypothesis Engine
  строит возможные объяснения.

Experiment Designer
  придумывает проверки.

Executor
  выполняет действия.

Policy Guard
  запрещает опасное.

Verifier
  доказывает успех/провал.

Rollback Manager
  откатывает изменения.

Memory Graph
  хранит факты, кейсы, рецепты.

Tool Forge
  создает новые инструменты.

Report Builder
  формирует понятный отчет.
```

---

# Самая сильная версия идеи

ИГЛА должна не просто «использовать модули».

Она должна делать так:

```text
1. Понять цель.
2. Построить локальную модель ситуации.
3. Сгенерировать несколько вариантов решения.
4. Спрогнозировать последствия каждого.
5. Выбрать самый безопасный.
6. Проверить гипотезы малыми экспериментами.
7. Выполнить обратимые действия.
8. Доказать результат.
9. Сохранить кейс.
10. Улучшить свои инструменты.
```

Вот это будет реально соответствовать названию.

---

# Главный вывод

Чтобы ИГЛА была настоящей ИГЛОЙ, её архитектура должна быть не такой:

```text
чат → ответ
```

и даже не такой:

```text
чат → tool call → ответ
```

а такой:

```text
цель
 ↓
локальное наблюдение
 ↓
гипотезы
 ↓
эксперименты
 ↓
доказательства
 ↓
действие с rollback
 ↓
верификация
 ↓
память
 ↓
новый навык
```

То есть ИГЛА — это:

```text
локальная самообучающаяся инженерная лаборатория,
которая использует ИИ как планировщик,
код как руки,
верификаторы как зрение,
а память как опыт.
```

И если делать её под твои реальные задачи — Gentoo, Quickemu, NVIDIA Workbench, vLLM, контейнеры, локальные модели, firewall, приватность — то это может быть не игрушка, а реально полезная система.


Я думаю, что нужно внести политику типа: нельзя изменить файл, не прочтя его; нельзя выполнить следующие действие до получения результатов предыдущего; нельзя приступать к следующему пункту, не прочтя TODO или не уточнив его и так далее. Нужна реально жесткая политика принуждения к проверке всего!
Касаемо хранения информации о системе - может быть использовать obsidian или что-то ему подобное, если есть? Но ИГЛА должна аккуратно и честко руководстоваться подобное системой памяти, чтобы не засорять контекст и четко архивировать воспоминания. Также нужно решить вопрос дедупликации информации, так как игла обладает контекстной информацией во время работы, но она же еще и как-то ищет информацию в системе памяти, а ее нужно дедуплицировать обязательно.
Как она будет кодить модули вообще не понятно, даже если я туда GPT OSS 120B посажу, то сильно я ей не доверяю, если честно.
ИГЛА должна также получать маасимально подробную и уточненную информацию об ошибке и вместо того, чтобы идти и писать херню еще раз, ей нужно запрещать это делать, пока она не пойдет искать информацию или в документацию или в систему памяти.
ИГЛА должна уметь очень вниательно и максимально эффективно работать со своим контекстом для выполнения особо больших поручений, например, анализа exel таблички на 2к строк и 700 столбцов и так далее. Это должен быть ОЧЕНЬ мощный алгоритм. 
Несомненно, часть tool'ов она получит сразу для работы, но системы регистрации и весь API должен быть блять настолько выдрочен до идеала, что он должен сиять просто!
Да и каждый модуль должен иметь ПОЛНУЮ обратную совместимость между каждым другим модулем, либо можно сделать так, чтобы каждый модуль был не куском более большого кода, который строит ИГЛА, а чтобы эти модули она могла вызывать с определнными параметрами, которые она получает через обращение к системе регистрации, получать от них вывод и вручную передавать эти аргументы или дальше или уточнять параметры следующего предполагаемого модуля и уже придумывать аргументы для него. 
Но как это контроллировать все в мега идеальной среде - я хз

Да, ты мыслишь в правильную сторону. Для ИГЛЫ нужна не «добрая инструкция модели», а **принудительная операционная система для агента**.

Модель должна быть не субъектом действия, а **генератором предложений**.
Реальный субъект действия — **IGLA Runtime**, который говорит:

```text
Нет.
Ты не читал файл.
Ты не получил результат предыдущего шага.
Ты не сверился с TODO.
Ты уже получил ошибку, значит теперь обязан диагностировать, а не патчить вслепую.
Ты пытаешься использовать память без дедупликации.
Ты пытаешься засунуть 2k×700 Excel целиком в контекст — запрещено.
```

Вот это и есть правильный уровень жесткости.

---

# 1. Главный принцип: модель ничего не делает напрямую

Архитектурно должно быть так:

```text
LLM proposes.
Runtime disposes.
```

То есть модель не вызывает `write_file`, `run_shell`, `edit_config`, `restart_container` напрямую.

Она может только выдать структурированное предложение:

```json
{
  "intent": "patch_file",
  "path": "server.py",
  "reason": "Need to fix missing import",
  "requires": [
    "file_read_receipt:server.py",
    "todo_checked",
    "previous_step_completed"
  ]
}
```

А дальше **Policy Kernel** решает: можно это выполнить или нет.

Для такого слоя полезны строгие структурированные ответы: Structured Outputs в OpenAI API как раз предназначены для того, чтобы модель возвращала данные по заданной JSON Schema, а не произвольный текст; это важно для agent-runtime, где каждый шаг должен быть валидируемым объектом, а не “ну я вроде хотел вызвать тул”. ([OpenAI Разработчики][1])

---

# 2. ИГЛА должна иметь “конституцию”

Я бы прямо сделал файл:

```text
igla/policies/constitution.yaml
```

Там не философия, а жесткие инварианты.

Например:

```yaml
policy_version: 1

global_invariants:
  - id: no_direct_tool_access
    rule: "LLM may only emit proposals. Tools are executed only by Runtime."

  - id: step_barrier
    rule: "No next action may execute until previous required action has completed and its result is stored."

  - id: read_before_write
    rule: "A file cannot be modified unless it has been read in the current task and its content hash is known."

  - id: hash_before_patch
    rule: "Every patch must specify the base file hash it was prepared against."

  - id: todo_before_execution
    rule: "Before modifying a project, Runtime must locate and read TODO/README/task files or record that none were found."

  - id: no_blind_retry
    rule: "After a failed action, retrying the same class of action is forbidden until diagnosis or documentation/memory search is completed."

  - id: evidence_required
    rule: "Every conclusion must reference evidence from tool output, file content, memory, or external documentation."

  - id: context_budget_required
    rule: "Large artifacts must be summarized/indexed before being exposed to LLM context."

  - id: reversible_by_default
    rule: "Mutating actions require backup, diff, rollback plan, or explicit exception."
```

Но важно: это не просто YAML для красоты.
Каждое правило должно быть проверяемым кодом.

---

# 3. Политика “нельзя изменить файл, не прочтя его”

Это надо реализовывать через **read receipt**.

Когда ИГЛА читает файл, Runtime создает квитанцию:

```json
{
  "type": "FileReadReceipt",
  "path": "/project/server.py",
  "sha256": "abc123...",
  "bytes_read": 18342,
  "read_mode": "full",
  "timestamp": "2026-04-29T00:00:00Z",
  "task_id": "task_001"
}
```

Потом любая операция изменения файла обязана сослаться на эту квитанцию:

```json
{
  "type": "PatchFileRequest",
  "path": "/project/server.py",
  "base_sha256": "abc123...",
  "patch": "...",
  "read_receipt_id": "receipt_777"
}
```

Runtime проверяет:

```text
1. Файл действительно был прочитан?
2. Был прочитан полностью или достаточно нужный диапазон?
3. Хеш файла на диске всё ещё совпадает?
4. Патч построен против актуальной версии?
5. Есть backup?
6. Есть diff?
7. Есть verifier после изменения?
```

Если файл изменился после чтения:

```text
DENY: stale_read_receipt
Reason: file hash changed after read. Re-read required.
```

Вот это железно.

---

# 4. Политика “нельзя выполнять следующий шаг без результата предыдущего”

Это делается через **state machine**, а не через просьбу к модели.

Каждый шаг имеет статус:

```text
PROPOSED
APPROVED
RUNNING
COMPLETED
FAILED
BLOCKED
ROLLED_BACK
```

Следующий шаг не может стартовать, если его зависимости не в `COMPLETED`.

Пример:

```json
{
  "step_id": "step_4",
  "action": "patch_file",
  "depends_on": ["step_1_read_file", "step_2_read_todo", "step_3_run_tests"],
  "status": "PROPOSED"
}
```

Runtime проверяет DAG:

```text
step_1_read_file: COMPLETED
step_2_read_todo: COMPLETED
step_3_run_tests: FAILED

=> step_4 BLOCKED
```

ИГЛА не может “забыть” дождаться результата, потому что шаг физически не будет выполнен.

---

# 5. Политика TODO: нельзя приступать, пока не понял задачу

Тут нужен отдельный gate:

```text
TODO_DISCOVERY_GATE
```

Перед изменением проекта Runtime обязан выполнить:

```text
1. Найти TODO.md, TASKS.md, README.md, CONTRIBUTING.md, docs/, issues/, pyproject, package metadata.
2. Найти inline TODO/FIXME/HACK в целевых файлах.
3. Найти task-файлы, если проект их использует.
4. Сформировать TaskContext.
5. Если задача противоречит TODO — остановиться и запросить уточнение.
```

Состояния:

```text
TODO_NOT_CHECKED       → запрещено менять проект
TODO_FOUND_AND_READ    → можно продолжать
TODO_NOT_FOUND_RECORDED → можно продолжать, но с записью evidence
TODO_CONFLICT          → нельзя продолжать без уточнения
```

Пример блокировки:

```text
DENY: todo_context_missing
Reason: project modification requested, but TODO/README/task discovery has not been completed.
Required action: run_project_intake
```

---

# 6. Политика после ошибки: запрет на тупой повтор

Это прям обязательно.

После любой ошибки система должна переходить в режим:

```text
FAILURE_DIAGNOSIS_REQUIRED
```

В этом режиме запрещены:

```text
- повтор той же команды без изменения причины;
- новый патч в тот же файл;
- установка зависимостей;
- изменение конфигов;
- “попробуем еще раз”;
- генерация нового кода поверх старого.
```

Разрешены только:

```text
- read_logs;
- inspect_state;
- search_memory;
- search_docs;
- classify_error;
- generate_hypotheses;
- ask_user_if_blocked;
```

Пример:

```text
Command failed:
  vllm serve ... --enable-ui

Next allowed actions:
  - inspect_command_help
  - search_local_memory("vllm --enable-ui")
  - search_official_docs("vllm serve arguments")
  - classify_cli_error

Forbidden:
  - edit_start_script
  - rerun_same_command
  - install_random_package
```

То есть ИГЛА должна быть вынуждена сначала ответить:

```text
Что именно сломалось?
Какая гипотеза?
Чем она подтверждается?
Где источник?
Какой минимальный эксперимент?
```

И только потом действовать.

---

# 7. Хранение памяти: Obsidian можно, но не как единственную память

Obsidian хорош как **человеко-читаемый слой памяти**: vault — это набор Markdown-файлов, а свойства заметок хранятся в YAML-формате в начале файла; это удобно для заметок, кейсов, рецептов, ручной проверки и редактирования. ([Obsidian][2])

Но Obsidian **не должен быть единственной базой памяти ИГЛЫ**.

Почему:

```text
Obsidian хорош для человека.
Но ИГЛЕ нужна машинная память:
- транзакции;
- индексы;
- дедупликация;
- версии фактов;
- связи;
- confidence;
- TTL;
- источники;
- быстрый retrieval;
- event log.
```

Поэтому я бы сделал так:

```text
Canonical Memory Store:
  SQLite/Postgres + vector index + graph-like relations

Human Memory Mirror:
  Obsidian vault в Markdown

Artifact Store:
  файлы, логи, отчеты, diff, таблицы, профили

Event Store:
  полная история действий ИГЛЫ
```

То есть Obsidian — это не “мозг”.
Obsidian — это **витрина и ручной редактор памяти**.

---

# 8. Правильная архитектура памяти

Я бы разделил память на 6 типов.

```text
1. Event Memory
   Всё, что произошло.
   Не редактируется.
   Источник истины для аудита.

2. Fact Memory
   Проверенные факты о системе.
   Например: QEMU version, CUDA version, paths.

3. Case Memory
   Решенные проблемы.
   Симптом → причина → решение → проверка.

4. Project Memory
   Состояние конкретных проектов.
   TODO, архитектура, зависимости, команды.

5. Preference Memory
   Твои устойчивые предпочтения.
   Например: подробные инструкции, rollback, без заглушек.

6. Working Context
   Временная память текущей задачи.
   Умирает или архивируется после завершения.
```

Ключевое правило:

```text
Контекст ≠ память.
Память ≠ лог.
Лог ≠ знание.
Знание появляется только после дедупликации, проверки и архивирования.
```

---

# 9. Дедупликация памяти

Это нужно делать не одной embedding-похожестью, а несколькими слоями.

## Слой 1 — точная дедупликация

Для каждого факта считаем canonical key:

```text
scope + subject + predicate + object + validity
```

Например:

```json
{
  "scope": "host:rog15",
  "subject": "quickemu",
  "predicate": "version",
  "object": "4.9.7"
}
```

Если такой факт уже есть — не добавляем дубль.

---

## Слой 2 — source hash

Если ИГЛА снова прочитала тот же лог/файл/документ:

```text
sha256(content) совпадает → не индексировать заново
```

---

## Слой 3 — semantic dedupe

Если формулировки разные, но смысл один:

```text
"vLLM does not support --enable-ui"
"Флаг --enable-ui отсутствует в текущей версии vLLM"
```

Тогда система должна создать не новую память, а новый источник к уже существующему факту:

```json
{
  "fact_id": "fact_vllm_no_enable_ui",
  "sources": [
    "local_help_output_2026_04_29",
    "case_memory_2026_04_20"
  ]
}
```

---

## Слой 4 — конфликт-детектор

Если новый факт противоречит старому:

```text
old: vLLM supports --enable-ui
new: vLLM does not support --enable-ui
```

Не затирать старое. Создать конфликт:

```json
{
  "type": "MemoryConflict",
  "subject": "vllm",
  "predicate": "supports_flag",
  "object": "--enable-ui",
  "old_value": true,
  "new_value": false,
  "resolution_required": true
}
```

Потом ИГЛА должна решить:

```text
- версия vLLM изменилась?
- старый факт был для другого окружения?
- факт был ошибочный?
- нужно уточнить scope?
```

То есть память должна быть **версионированной и scoped**, а не просто кучей заметок.

---

# 10. Как ИГЛА должна использовать Obsidian

Я бы сделал vault примерно такой:

```text
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

Каждая заметка должна иметь YAML:

```yaml
---
type: case
scope: spark-gb10
status: verified
confidence: high
created: 2026-04-29
updated: 2026-04-29
canonical_id: case_vllm_enable_ui_error
dedupe_key: vllm:error:unrecognized_argument:enable-ui
sources:
  - event:evt_001
  - log:/workspace/logs/vllm/server.log
tags:
  - vllm
  - workbench
  - cli-error
---
```

ИГЛА не должна просто “писать воспоминания”.
Она должна делать pipeline:

```text
raw event
  ↓
candidate memory
  ↓
dedupe
  ↓
conflict check
  ↓
canonical memory
  ↓
Obsidian mirror
```

---

# 11. Контекст: как не засрать окно модели

Для больших задач ИГЛА должна иметь **Context Manager**.

Правило:

```text
В модель нельзя пихать весь мир.
Модель получает только рабочий пакет контекста.
```

Контекст должен делиться на уровни:

```text
L0: текущее задание и критерии успеха
L1: последние действия и результаты
L2: релевантные выдержки из файлов
L3: краткая карта проекта
L4: retrieved memory
L5: полные артефакты доступны только через tools
```

Модель должна видеть не “весь файл”, а:

```json
{
  "context_pack": {
    "task": "...",
    "current_state": "...",
    "relevant_evidence": [...],
    "open_questions": [...],
    "allowed_next_actions": [...]
  }
}
```

И когда ей нужен полный файл, она не фантазирует, а запрашивает:

```json
{
  "action": "read_file",
  "path": "server.py",
  "reason": "Need full context before patching import section"
}
```

---

# 12. Большие Excel-таблицы: модель не должна их читать целиком

Для таблицы 2000 строк × 700 столбцов нельзя делать так:

```text
загрузить Excel → скормить всё LLM → спросить мнение
```

Это мусорная архитектура.

Нужно так:

```text
Excel file
  ↓
deterministic profiler
  ↓
schema map
  ↓
column groups
  ↓
statistics
  ↓
anomaly index
  ↓
query planner
  ↓
targeted slices
  ↓
LLM analysis only on summaries/slices
```

ИГЛА должна сначала построить профиль:

```text
- список листов;
- размеры;
- названия столбцов;
- типы данных;
- процент пустых;
- уникальные значения;
- min/max/mean для чисел;
- частотные значения для категорий;
- формулы;
- дубликаты;
- выбросы;
- корреляции;
- подозрительные столбцы;
- группы похожих колонок;
- sample rows;
- data quality report.
```

Потом LLM получает не 1.4 млн ячеек, а:

```text
1. карту таблицы;
2. список проблем;
3. релевантные колонки;
4. агрегаты;
5. маленькие срезы;
6. ссылки на артефакты.
```

Если пользователь спрашивает:

```text
Найди странности в таблице
```

ИГЛА делает pipeline:

```text
profile_excel
detect_schema
detect_missingness
detect_outliers
detect_duplicates
detect_formula_errors
cluster_columns
summarize_findings
ask_llm_to_interpret
generate_report
```

LLM — не вычислитель таблицы.
LLM — интерпретатор уже посчитанных фактов.

---

# 13. API модулей: модули не должны “срастаться” напрямую

Ты правильно чувствуешь проблему.
Нельзя делать так, чтобы ИГЛА генерировала один большой кусок кода, где модуль A напрямую импортирует внутренности модуля B.

Правильная модель:

```text
Модули не знают друг друга.
Модули знают только общий протокол.
```

Каждый модуль — это независимый инструмент:

```text
input schema → execution → output schema
```

Между ними ходят не Python-объекты, а **типизированные артефакты**.

Например:

```json
{
  "artifact_id": "artifact_excel_profile_001",
  "artifact_type": "ExcelProfile",
  "schema_version": "1.0.0",
  "location": "artifacts/excel/profile_001.json",
  "summary": "Workbook with 3 sheets, 700 columns, 2000 rows"
}
```

Следующий модуль получает не “результат как попало”, а ссылку:

```json
{
  "tool": "detect_excel_anomalies",
  "args": {
    "profile_artifact": "artifact_excel_profile_001"
  }
}
```

Это и есть нормальная совместимость.

---

# 14. Tool Registry должен быть священным

Registry — это сердце ИГЛЫ.

Не просто список функций, а полноценный каталог возможностей:

```yaml
name: profile_excel
version: 1.2.0
description: "Analyze workbook structure and generate deterministic profile"
risk_level: read_only
side_effects: false
input_schema: schemas/ProfileExcelInput.json
output_schema: schemas/ExcelProfileArtifact.json
produces:
  - ExcelProfile
consumes:
  - ExcelWorkbook
requires:
  - file_read_receipt
timeouts:
  default_seconds: 120
resource_limits:
  max_memory_mb: 4096
compatibility:
  min_runtime: "0.4.0"
  output_schema_version: "1.x"
verifier:
  type: schema_validation
```

Для mutating-инструмента:

```yaml
name: patch_file
version: 1.0.0
risk_level: mutating
side_effects: true
requires:
  - file_read_receipt
  - base_hash
  - backup_plan
  - verifier_plan
rollback:
  supported: true
  strategy: restore_backup
```

Registry должен отвечать на вопросы:

```text
Какие tools существуют?
Что они принимают?
Что возвращают?
Какие артефакты производят?
Опасны ли они?
Можно ли их запускать автоматически?
Какие политики нужны перед запуском?
Чем проверить результат?
Чем откатить?
```

MCP можно использовать как вдохновение или внешний совместимый слой: спецификация MCP разделяет capabilities на resources, prompts и tools — resources дают контекст/данные, prompts задают шаблоны взаимодействия, tools являются функциями, которые модель может просить выполнить. ([Model Context Protocol][3])

Но для ИГЛЫ я бы не полагался только на MCP.
Я бы сделал **свой внутренний Tool Registry**, а MCP — как адаптер наружу.

---

# 15. Совместимость модулей

Полной совместимости “каждого с каждым” в смысле “любой модуль напрямую дружит с любым” лучше не делать. Это приведет к аду.

Нужна другая совместимость:

```text
Все модули совместимы через общий протокол артефактов.
```

То есть:

```text
Module A produces Artifact X.
Module B consumes Artifact X.
Runtime проверяет schema_version.
```

Например:

```text
read_excel → ExcelWorkbookArtifact
profile_excel → ExcelProfileArtifact
detect_anomalies → AnomalyReportArtifact
generate_report → MarkdownReportArtifact
create_docx → DocxArtifact
```

Так модули не “срастаются”, а соединяются как трубы.

---

# 16. Версионирование артефактов

Каждый output должен иметь версию схемы:

```json
{
  "artifact_type": "ExcelProfile",
  "schema_version": "1.1.0",
  "producer": "profile_excel@1.2.0",
  "created_at": "...",
  "content": {}
}
```

Правила:

```text
1. Patch version: совместимо.
2. Minor version: совместимо назад.
3. Major version: нужен adapter/migration.
```

Если модуль хочет старую схему, Runtime может вызвать adapter:

```text
ExcelProfile v2 → adapter → ExcelProfile v1
```

Вот это и есть нормальная “обратная совместимость”.

---

# 17. Как контролировать кодинг новых модулей

Ты прав: нельзя доверять даже GPT-OSS 120B как автору боевого кода.

Правильная роль модели:

```text
LLM = junior developer, который пишет proposal.
Runtime/test/security/review = senior engineer.
```

ИГЛА может генерировать модуль только через pipeline:

```text
1. Tool Need Detection
   "Мне не хватает инструмента X."

2. Tool Proposal
   Описание назначения, input/output schema, риски.

3. Human/Policy Approval
   Можно ли вообще создавать такой tool?

4. Generate Code in Quarantine
   Код кладется не в tools/, а в quarantine/.

5. Generate Tests
   Unit tests, property tests, schema tests.

6. Static Checks
   lint, type check, forbidden imports, dangerous calls.

7. Sandbox Run
   Запуск на тестовых данных.

8. Compatibility Check
   Проверка manifest, schemas, versioning.

9. Security Review
   Нет ли доступа к сети, shell, secrets, filesystem outside scope.

10. Promotion
   Только после этого tool попадает в registry.
```

То есть модель не “добавила себе способность”.
Она **предложила кандидата**, прошла CI, и только потом Runtime его зарегистрировал.

---

# 18. Запреты для сгенерированных модулей

Для AI-generated tools я бы ввел жесткий blacklist.

По умолчанию запрещены:

```text
- subprocess;
- os.system;
- shell=True;
- socket/network;
- requests/httpx;
- чтение env без whitelist;
- доступ за пределы workspace;
- удаление файлов;
- chmod/chown;
- sudo;
- docker socket;
- запись в registry;
- запись в memory напрямую.
```

Если модулю реально нужен shell, он не получает его напрямую.
Он вызывает отдельный runtime tool:

```text
safe_command_executor
```

А тот уже проходит policy.

---

# 19. Реальная структура ИГЛЫ

Я бы разделил на 3 больших слоя.

```text
┌─────────────────────────────────────────────┐
│                IGLA UI                       │
│ CLI / Web / Obsidian mirror / API            │
└─────────────────────┬───────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│             IGLA Orchestrator                │
│ LLM planning / context manager / memory use  │
└─────────────────────┬───────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│              IGLA Kernel                     │
│ Policy / state machine / registry / executor │
└─────────────────────┬───────────────────────┘
                      ↓
┌─────────────────────────────────────────────┐
│              Tools & Artifacts               │
│ files / shell / docker / excel / git / docs   │
└─────────────────────────────────────────────┘
```

Очень важно:

```text
Orchestrator может ошибаться.
Kernel не должен ошибаться.
```

Поэтому Kernel лучше писать максимально строго.
Python можно оставить для orchestration и LLM-логики, а ядро политик/исполнения со временем вынести в Rust.

---

# 20. Минимальный цикл исполнения

Правильный цикл должен быть таким:

```text
1. User request
2. Task intake
3. TODO/project/context discovery
4. Memory retrieval
5. Plan proposal
6. Policy validation
7. Step execution
8. Evidence capture
9. Verifier
10. State update
11. Next step decision
12. Final report
13. Memory distillation
14. Archive
```

Не так:

```text
User → LLM → shell → oops
```

А так:

```text
User → TaskSpec → PlanProposal → PolicyGate → Executor → Evidence → Verifier → Memory
```

---

# 21. Action Gate Matrix

Я бы сделал матрицу разрешений.

```text
Action: read_file
Requires:
  - path_allowed

Action: patch_file
Requires:
  - file_read_receipt
  - current_hash == base_hash
  - backup_created
  - todo_checked
  - verifier_declared

Action: run_shell
Requires:
  - command_classified
  - risk_assessed
  - timeout_set
  - working_dir_set
  - no_forbidden_tokens
  - approval if mutating

Action: install_package
Requires:
  - explicit approval
  - package_source_verified
  - rollback_or_snapshot
  - reason linked to error

Action: retry_after_failure
Requires:
  - failure_classified
  - memory_or_docs_checked
  - changed_condition_declared

Action: write_memory
Requires:
  - source_event
  - dedupe_checked
  - confidence_assigned
  - scope_assigned
```

То есть любое действие — это не “можно/нельзя”, а набор предикатов.

---

# 22. Evidence-first подход

Каждый вывод ИГЛЫ должен быть связан с evidence.

Плохо:

```text
Похоже, проблема в версии vLLM.
```

Хорошо:

```text
Гипотеза: текущая версия vLLM не поддерживает --enable-ui.

Evidence:
- команда vllm serve завершилась с "unrecognized arguments: --enable-ui";
- локальный vllm serve --help не содержит --enable-ui;
- в памяти найден прошлый кейс с таким же симптомом.

Verdict:
- гипотеза подтверждена.
```

Внутри это должно быть объектом:

```json
{
  "claim": "vLLM does not support --enable-ui in this environment",
  "confidence": "high",
  "evidence": [
    "event:cmd_001",
    "event:cmd_002",
    "memory:case_vllm_enable_ui_error"
  ],
  "scope": "container:workbench-vllm"
}
```

Без evidence claim не должен попадать в финальный отчет и память.

---

# 23. Как ИГЛА должна работать с документацией

После ошибки система должна выбирать:

```text
1. local evidence;
2. local memory;
3. project docs;
4. official docs;
5. web/issues;
6. only then model inference.
```

Например:

```text
FAILED: pytest
  ↓
read traceback
  ↓
search project docs
  ↓
search memory
  ↓
inspect package version
  ↓
search official docs
  ↓
generate patch
```

Главное правило:

```text
После ошибки модель не имеет права сразу писать новый код.
```

Она должна сначала получить новый факт.

---

# 24. Работа с памятью во время задачи

У ИГЛЫ должен быть `Working Set`.

Например:

```json
{
  "task_id": "task_123",
  "active_facts": [
    "fact_qemu_version",
    "fact_ovmf_path",
    "case_quickemu_ovmf"
  ],
  "active_files": [
    "windows-11.conf",
    "run-vm.sh"
  ],
  "active_evidence": [
    "event_read_conf",
    "event_qemu_log"
  ],
  "context_budget": {
    "max_tokens": 32000,
    "used_tokens": 12000
  }
}
```

ИГЛА не должна каждый раз искать всю память.
Она должна:

```text
1. найти кандидатов;
2. дедуплицировать;
3. выбрать top-N;
4. собрать context pack;
5. держать active working set;
6. при переполнении — compress/handoff.
```

---

# 25. Контекст для больших поручений

Для долгих задач нужно не “держать всё в голове”, а вести **Task Ledger**.

```text
Task Ledger:
  - цель;
  - критерии успеха;
  - открытые вопросы;
  - сделанные шаги;
  - текущие гипотезы;
  - важные evidence;
  - запреты;
  - следующий разрешенный шаг.
```

Когда контекст переполняется, ИГЛА делает не обычное summary, а **state checkpoint**:

```json
{
  "goal": "...",
  "completed_steps": [...],
  "failed_steps": [...],
  "current_blockers": [...],
  "verified_facts": [...],
  "unverified_assumptions": [...],
  "next_allowed_actions": [...],
  "forbidden_actions": [...]
}
```

После этого модель может продолжить без потери контроля.

---

# 26. “Мега идеальная среда” — это не один агент, а Runtime

Идеальная среда выглядит так:

```text
LLM
  ↓ proposes only
Plan Validator
  ↓
Policy Kernel
  ↓
State Machine
  ↓
Tool Registry
  ↓
Sandbox Executor
  ↓
Evidence Store
  ↓
Verifier
  ↓
Memory Curator
```

Модель — заменяемая.
Можно поставить GPT-OSS 120B, Claude, GPT, локальную модель, ансамбль — неважно.

Потому что безопасность и качество держатся не на “модель умная”, а на:

```text
- строгом протоколе;
- запретах;
- state machine;
- evidence;
- verifier;
- rollback;
- memory hygiene;
- schema validation.
```

---

# 27. Самый важный дизайн: Allowed Next Actions

В каждый момент Runtime должен отдавать модели не “делай что хочешь”, а список разрешенных следующих действий.

Например после чтения файла:

```json
{
  "allowed_next_actions": [
    "read_related_file",
    "search_memory",
    "propose_patch",
    "run_static_check"
  ],
  "forbidden_next_actions": [
    "write_file_without_patch",
    "run_shell_without_reason",
    "final_answer_without_verification"
  ]
}
```

После ошибки:

```json
{
  "allowed_next_actions": [
    "classify_error",
    "read_logs",
    "search_memory",
    "search_docs",
    "propose_minimal_experiment"
  ],
  "forbidden_next_actions": [
    "patch_file",
    "install_package",
    "rerun_same_command",
    "declare_success"
  ]
}
```

Это радикально снижает хаос.

---

# 28. Что делать с “модель пишет херню”

Запрещать не “плохие ответы”, а **недоказанные действия**.

Модель может написать херню в reasoning/proposal, но Runtime скажет:

```text
Rejected:
- no evidence
- stale file read
- no TODO check
- no verifier
- repeated failed action
- undocumented assumption
```

И вернет модели структурированную ошибку:

```json
{
  "rejection": "NO_BLIND_RETRY",
  "reason": "Previous command failed. You attempted another patch without diagnosing the failure.",
  "required_next_action": [
    "classify_error",
    "search_memory",
    "search_docs",
    "inspect_logs"
  ]
}
```

Модель должна не спорить, а выбрать один из allowed actions.

---

# 29. Пример полного сценария

Пользователь:

```text
ИГЛА, почини запуск vLLM.
```

Runtime:

```text
Task intake required.
```

ИГЛА делает:

```text
1. read_project_tree
2. find TODO/README/docs
3. read startup scripts
4. read logs
5. inspect environment
6. search memory
7. classify error
```

Допустим, ошибка:

```text
unrecognized arguments: --enable-ui
```

Дальше запрещено сразу патчить.

Нужно:

```text
8. run vllm serve --help
9. compare supported args
10. create hypothesis
11. propose patch
12. policy check: script was read? hash current? backup?
13. patch
14. run dry test
15. start service
16. check /v1/models
17. write case memory
```

Вот это нормальное поведение.

---

# 30. Первый MVP, который реально стоит делать

Я бы начинал не с самогенерации модулей.
Начать надо с **железного ядра политики**.

## MVP-0: read-only ИГЛА

Умеет:

```text
- принимать задачу;
- строить TaskSpec;
- читать файлы;
- читать TODO/README;
- читать логи;
- искать память;
- строить гипотезы;
- выдавать план;
- ничего не менять.
```

## MVP-1: Policy Kernel

Добавить:

```text
- read receipts;
- step barrier;
- failure diagnosis mode;
- evidence store;
- allowed next actions;
- task ledger.
```

## MVP-2: reversible edits

Добавить:

```text
- patch_file only after read;
- backup;
- diff;
- verifier;
- rollback.
```

## MVP-3: memory system

Добавить:

```text
- event store;
- fact memory;
- case memory;
- dedupe;
- Obsidian mirror.
```

## MVP-4: artifact protocol

Добавить:

```text
- typed artifacts;
- schemas;
- module registry;
- versioning;
- adapters.
```

## MVP-5: generated tools quarantine

Только потом:

```text
- tool proposal;
- generated code;
- tests;
- sandbox;
- promotion to registry.
```

---

# 31. Главная формула ИГЛЫ

Я бы сформулировал так:

```text
ИГЛА — это не LLM-agent.
ИГЛА — это проверяющий runtime, в котором LLM является заменяемым планировщиком.
```

Её сила должна быть не в том, что модель “очень умная”, а в том, что она **зажата в правильные рельсы**:

```text
прочитал → доказал → сделал → проверил → сохранил
```

И если не прочитал:

```text
не сделал
```

Если не проверил:

```text
не заявил успех
```

Если получил ошибку:

```text
не патчишь, пока не диагностировал
```

Если память уже содержит факт:

```text
не дублируешь
```

Если файл большой:

```text
не пихаешь в контекст, а индексируешь
```

Если модуль сгенерирован моделью:

```text
не доверяешь, пока не прошел quarantine/CI
```

---

# 32. Самый правильный технический вывод

Тебе нужна не “идеальная модель”.
Тебе нужна **идеальная среда принуждения**.

Минимальное ядро:

```text
IGLA Kernel:
  - Policy Engine
  - State Machine
  - Tool Registry
  - Artifact Store
  - Evidence Store
  - Memory Curator
  - Context Manager
  - Verifier
  - Rollback Manager
```

А модель — это только:

```text
Planner
Hypothesis generator
Argument builder
Report writer
```

Вот тогда даже если GPT-OSS 120B иногда тупит, она не сможет развалить систему.
Она будет упираться в стены Runtime и вынуждена будет работать правильно.

[1]: https://developers.openai.com/api/docs/guides/structured-outputs?utm_source=chatgpt.com "Structured model outputs | OpenAI API"
[2]: https://obsidian.md/help/properties?utm_source=chatgpt.com "Properties - Obsidian Help"
[3]: https://modelcontextprotocol.io/specification/2025-11-25?utm_source=chatgpt.com "Specification"


Бля, даже с моей архитектурной фанатзией я просто ДАЖЕ БЛИЗКО не представляю, как это накодить...просто не знаю и все. Это такой пиздец по логике и всем модулям...

Это нормальная реакция. Потому что ты сейчас в голове пытаешься сразу написать **всю ИГЛУ целиком**:

```text
агент
память
политики
модули
sandbox
Excel-анализ
кодогенерация
дедупликация
Obsidian
версионирование
rollback
registry
верификаторы
```

Так это действительно выглядит как пиздец.

Но её **не надо кодить целиком**.

Её надо начинать как **очень тупой, очень строгий runtime на 3 действия**.

Не “ИИ, которая всё умеет”, а:

```text
1. read_file
2. patch_file
3. run_command
```

И одно железное правило:

```text
Нельзя patch_file, если read_file не был выполнен раньше.
```

Всё. Это первый кирпич. Не агент. Не память. Не Obsidian. Не GPT-OSS 120B. Просто маленькая машина принуждения.

---

# С чего реально начать

Первую версию я бы сделал так:

```text
igla/
├── main.py
├── kernel/
│   ├── state.py
│   ├── policy.py
│   ├── actions.py
│   └── executor.py
├── tools/
│   ├── file_tools.py
│   └── shell_tools.py
└── workspace/
```

Идея:

```text
LLM вообще пока нет.
Памяти пока нет.
Obsidian пока нет.
Самописных модулей пока нет.

Есть только Runtime, который умеет:
- читать файл;
- запоминать, что он был прочитан;
- запрещать изменение непрочитанного файла;
- делать backup перед изменением;
- писать event log.
```

Это уже основа ИГЛЫ.

---

# Минимальная первая цель

Не “сделать ИГЛУ”.

А вот это:

```text
Сделать программу, которая физически не даст изменить файл, пока он не прочитан.
```

Пример поведения:

```text
> patch server.py
DENY: file was not read in this task

> read server.py
OK: read receipt created

> patch server.py
OK: backup created, patch applied
```

Вот это первый настоящий кусок.

---

# Самое первое ядро

Примерно так:

```python
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import shutil
import time


@dataclass
class ReadReceipt:
    path: str
    sha256: str
    timestamp: float


@dataclass
class RuntimeState:
    read_receipts: dict[str, ReadReceipt] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)


class PolicyError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class IGLAKernel:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.state = RuntimeState()

    def _safe_path(self, path: str) -> Path:
        full = (self.workspace / path).resolve()

        if not str(full).startswith(str(self.workspace)):
            raise PolicyError("DENY: path escapes workspace")

        return full

    def read_file(self, path: str) -> str:
        full = self._safe_path(path)

        if not full.exists():
            raise PolicyError(f"DENY: file does not exist: {path}")

        content = full.read_text(encoding="utf-8")
        digest = sha256_file(full)

        self.state.read_receipts[str(full)] = ReadReceipt(
            path=str(full),
            sha256=digest,
            timestamp=time.time(),
        )

        self.state.events.append({
            "type": "read_file",
            "path": str(full),
            "sha256": digest,
            "timestamp": time.time(),
        })

        return content

    def patch_file_replace(self, path: str, old: str, new: str) -> None:
        full = self._safe_path(path)
        receipt = self.state.read_receipts.get(str(full))

        if receipt is None:
            raise PolicyError("DENY: file was not read before patch")

        current_hash = sha256_file(full)

        if current_hash != receipt.sha256:
            raise PolicyError("DENY: file changed after read; re-read required")

        content = full.read_text(encoding="utf-8")

        if old not in content:
            raise PolicyError("DENY: old text not found in file")

        backup = full.with_suffix(full.suffix + f".bak.{int(time.time())}")
        shutil.copy2(full, backup)

        patched = content.replace(old, new, 1)
        full.write_text(patched, encoding="utf-8")

        self.state.events.append({
            "type": "patch_file",
            "path": str(full),
            "backup": str(backup),
            "old_hash": current_hash,
            "new_hash": sha256_file(full),
            "timestamp": time.time(),
        })
```

Это не “вау-ИИ”.
Но это уже **ИГЛА-ядро**.

Потому что тут есть главное:

```text
политика сильнее модели
```

---

# Почему начинать надо именно так

Потому что весь твой монстр раскладывается на маленькие запреты.

Не надо сразу писать:

```text
универсальную систему дедупликации памяти
```

Надо сначала написать:

```text
write_memory запрещен, если нет source_event
```

Не надо сразу писать:

```text
идеальный Tool Registry
```

Надо сначала написать:

```text
tool нельзя вызвать, если его input не проходит schema validation
```

Не надо сразу писать:

```text
самогенерацию модулей
```

Надо сначала написать:

```text
generated module нельзя зарегистрировать без manifest.json
```

Не надо сразу писать:

```text
идеальный Excel-анализатор
```

Надо сначала написать:

```text
Excel-файл нельзя отдавать модели целиком; сначала profile_excel
```

То есть ИГЛА — это не одна большая программа.
Это **сотня маленьких “нельзя, пока не...”**.

---

# Реальный порядок разработки

Я бы делал в таком порядке.

## Этап 1. Kernel без ИИ

Цель:

```text
строгий исполнитель действий
```

Действия:

```text
read_file
list_dir
patch_file
run_command
```

Политики:

```text
нельзя patch_file без read_file
нельзя patch_file, если файл изменился после чтения
нельзя run_command без timeout
нельзя выходить за workspace
все действия пишутся в event log
```

Это уже полезная программа.

---

## Этап 2. Event Log

Каждое действие пишет JSONL:

```json
{"type":"read_file","path":"server.py","sha256":"...","time":"..."}
{"type":"patch_file","path":"server.py","backup":"server.py.bak","time":"..."}
{"type":"run_command","cmd":"pytest","exit_code":1,"time":"..."}
```

Это фундамент памяти, аудита, дедупликации и анализа.

Без event log нельзя строить ИГЛУ.

---

## Этап 3. Tool Registry

Пока простой:

```python
TOOLS = {
    "read_file": {
        "risk": "read_only",
        "requires": [],
    },
    "patch_file": {
        "risk": "mutating",
        "requires": ["file_read_receipt", "backup"],
    },
    "run_command": {
        "risk": "execution",
        "requires": ["timeout", "working_dir"],
    },
}
```

Не надо сразу идеальный registry.
Сначала обычный словарь.

---

## Этап 4. State Machine

Ввести статусы:

```text
READY
WAITING_FOR_TOOL_RESULT
FAILURE_DIAGNOSIS_REQUIRED
TASK_DONE
BLOCKED
```

После ошибки команда переводит Runtime в:

```text
FAILURE_DIAGNOSIS_REQUIRED
```

И там разрешены только:

```text
read_file
read_log
search_memory
inspect_command
```

А `patch_file` запрещен.

---

## Этап 5. LLM как планировщик

Только теперь подключать модель.

Но модель должна возвращать не текст, а JSON:

```json
{
  "action": "read_file",
  "args": {
    "path": "server.py"
  },
  "reason": "Need to inspect the file before proposing a patch"
}
```

Runtime принимает или отклоняет.

---

# Как не утонуть

Тебе не нужно держать в голове всю ИГЛУ.

Надо держать только текущий инвариант.

Например, первая неделя:

```text
Инвариант №1:
нельзя изменить файл, не прочтя его.
```

Вторая неделя:

```text
Инвариант №2:
нельзя делать следующий шаг, пока предыдущий не завершен.
```

Третья неделя:

```text
Инвариант №3:
после ошибки нельзя патчить, пока не проведена диагностика.
```

Четвертая:

```text
Инвариант №4:
любой вывод должен ссылаться на evidence.
```

Пятая:

```text
Инвариант №5:
память нельзя записать без source_event и dedupe_key.
```

Так оно становится реализуемым.

---

# Не начинай с Obsidian

Obsidian — потом.

Сначала:

```text
events.jsonl
state.json
artifacts/
```

Пример:

```text
.igla/
├── events.jsonl
├── state.json
├── receipts.json
├── artifacts/
│   ├── command_outputs/
│   ├── file_snapshots/
│   └── reports/
└── memory/
    ├── facts.jsonl
    └── cases.jsonl
```

Obsidian можно подключить позже как mirror:

```text
memory/cases.jsonl → Obsidian Markdown
```

Но если начать с Obsidian, ты залипнешь в структуру заметок и не напишешь ядро.

---

# Не начинай с самописания модулей

Кодогенерация модулей — это поздний этап.

Пока ИГЛА не умеет:

```text
читать
запрещать
логировать
делать backup
проверять
откатывать
```

ей нельзя доверять генерацию модулей.

Первый годный подход:

```text
Все модули пишешь ты.
ИГЛА только вызывает их.
```

Потом:

```text
ИГЛА предлагает код модуля.
Но не регистрирует его автоматически.
```

Еще позже:

```text
quarantine → tests → manifest → approval → registry
```

---

# Самая простая модель всей системы

Вот это надо зафиксировать:

```text
IGLA = Kernel + Tools + Memory + LLM
```

Но порядок реализации:

```text
1. Kernel
2. Tools
3. Event log
4. Verifiers
5. Memory
6. LLM
7. Generated tools
```

Не наоборот.

Многие агентные системы ломаются потому, что начинают с LLM.
Тебе надо начать с **тюремщика для LLM**.

---

# Что именно писать первым

Первый нормальный milestone:

```text
igla run task.json
```

Где `task.json`:

```json
{
  "goal": "Replace hello with hi in test.txt",
  "steps": [
    {
      "action": "patch_file",
      "args": {
        "path": "test.txt",
        "old": "hello",
        "new": "hi"
      }
    }
  ]
}
```

И Runtime должен ответить:

```text
DENY: patch_file requires read_file receipt
```

Потом такой task:

```json
{
  "goal": "Replace hello with hi in test.txt",
  "steps": [
    {
      "action": "read_file",
      "args": {
        "path": "test.txt"
      }
    },
    {
      "action": "patch_file",
      "args": {
        "path": "test.txt",
        "old": "hello",
        "new": "hi"
      }
    }
  ]
}
```

И Runtime делает:

```text
OK: read_file test.txt
OK: backup created
OK: patch applied
```

Вот это первый успех.

---

# Почему это уже не игрушка

Потому что потом `task.json` будет писать LLM.

Но Runtime останется тем же.

Сегодня:

```text
task.json написал ты
```

Завтра:

```text
task.json написала модель
```

Но если модель написала плохой план, Runtime скажет:

```text
DENY
```

Вот и вся магия.

---

# Главное успокоение

Тебе не надо знать, как накодить всю ИГЛУ.

Нужно знать, как накодить первый запрет.

```text
Нельзя patch без read.
```

Потом второй.

```text
Нельзя следующий step без результата предыдущего.
```

Потом третий.

```text
Нельзя retry после ошибки без диагностики.
```

И через 10 таких запретов у тебя уже не “чат-бот”, а настоящий Agent Runtime.

---

# Самая правильная стартовая формула

Не пиши:

```text
IGLA AI system
```

Пиши:

```text
igla-policy-kernel
```

Первый README:

```text
IGLA Policy Kernel is a local agent runtime that enforces safe task execution.

It does not trust the model.
It does not execute unverified actions.
It records every event.
It requires evidence before mutation.
```

Вот с этого реально можно начать.

И да: это огромная система. Но она становится нестрашной, когда ты перестаешь пытаться написать “разум” и начинаешь писать **замки на двери**.


Просто я хочу продумать заранее многопараметровое API ядра, регистраторов и прочего, чтобы не переписывать API модулей по 10 раз

Да, вот это уже **правильный момент для продумывания API**. Не надо сейчас кодить всю ИГЛУ, но **границы ядра, модулей, registry, артефактов и политик** лучше заложить заранее. Иначе потом реально будешь переписывать tool API по 10 раз.

Но важный момент:

**Не надо заранее придумывать 100 параметров для каждого модуля.**
Нужно придумать **один стабильный протокол**, через который любые модули общаются с ядром.

То есть не так:

```python
profile_excel(path, sheet, rows, columns, mode, memory, policy, context, ...)
```

А так:

```python
tool.invoke(invocation: ToolInvocation) -> ToolResult
```

Всё.
Один вход. Один выход. Остальное живёт внутри структурированного JSON/Pydantic-объекта.

---

# Главная идея стабильного API

Нужно заморозить не сами модули, а **форму общения**:

```text
Kernel → ToolInvocation → Tool
Tool → ToolResult → Kernel
```

Модуль не знает про другие модули.
Модуль не вызывает другие модули напрямую.
Модуль не пишет в память напрямую.
Модуль не меняет state напрямую.

Он только:

```text
получил входной envelope
сделал свою работу
вернул output envelope
```

А всё остальное делает ядро.

---

# Три уровня API

Я бы разделил API ИГЛЫ на три слоя.

```text
1. Kernel API
   Внутренние сервисы ядра: policy, registry, artifacts, events, memory.

2. Tool Protocol API
   Универсальный протокол вызова модулей.

3. Domain Schemas
   Конкретные схемы для Excel, файлов, Docker, Git, vLLM, Quickemu и т.д.
```

Самое важное: **Tool Protocol API должен быть максимально стабильным**.

А вот domain schemas могут эволюционировать.

---

# Самый важный контракт: ToolInvocation

Каждый модуль должен принимать один объект:

```json
{
  "protocol_version": "1.0",
  "invocation_id": "inv_01HX...",
  "task_id": "task_01HX...",
  "step_id": "step_003",

  "tool": {
    "name": "profile_excel",
    "version": "1.2.0"
  },

  "input": {},

  "context": {
    "workspace_id": "default",
    "working_dir": "/workspace/project",
    "artifact_scope": "task",
    "mode": "normal"
  },

  "policy": {
    "risk_level": "read_only",
    "allowed_paths": ["/workspace/project"],
    "network": "disabled",
    "requires_approval": false,
    "max_runtime_seconds": 120,
    "max_memory_mb": 4096
  },

  "dependencies": {
    "required_receipts": [],
    "required_artifacts": [],
    "required_evidence": []
  },

  "provenance": {
    "requested_by": "planner",
    "reason": "Need deterministic profile before analyzing workbook",
    "parent_step_id": "step_002"
  },

  "extensions": {}
}
```

Ключевой момент: **верхний envelope почти никогда не меняется**.

Меняется только:

```json
"input": {}
```

А его схема зависит от конкретного tool.

---

# Второй важный контракт: ToolResult

Каждый модуль должен возвращать один объект:

```json
{
  "protocol_version": "1.0",
  "invocation_id": "inv_01HX...",
  "task_id": "task_01HX...",
  "step_id": "step_003",

  "status": "success",

  "output": {},

  "artifacts": [],

  "evidence": [],

  "receipts": [],

  "logs": [],

  "metrics": {
    "runtime_ms": 742,
    "memory_peak_mb": 120
  },

  "error": null,

  "next_hints": []
}
```

Если ошибка:

```json
{
  "status": "failed",
  "error": {
    "kind": "ValidationError",
    "code": "EXCEL_FILE_NOT_FOUND",
    "message": "Input artifact points to a missing file",
    "retryable": false,
    "requires_diagnosis": true,
    "details": {}
  }
}
```

Вот эта структура критически важна.
Именно по `error.kind`, `retryable`, `requires_diagnosis` ядро будет понимать:

```text
можно ли повторить;
нужно ли искать документацию;
нужно ли читать память;
нужно ли заблокировать patch;
можно ли идти дальше.
```

---

# Нельзя делать API вида “функция вызывает функцию”

Плохо:

```python
excel_profile = profile_excel("file.xlsx")
anomalies = detect_anomalies(excel_profile)
report = generate_report(anomalies)
```

Почему плохо:

```text
- модули начинают зависеть друг от друга;
- сложно версионировать;
- сложно логировать;
- сложно проверять policy;
- сложно откатывать;
- сложно воспроизводить цепочку.
```

Хорошо:

```text
profile_excel
  consumes: ExcelWorkbookArtifact
  produces: ExcelProfileArtifact

detect_excel_anomalies
  consumes: ExcelProfileArtifact
  produces: ExcelAnomalyReportArtifact

generate_markdown_report
  consumes: ExcelAnomalyReportArtifact
  produces: MarkdownReportArtifact
```

То есть модули связаны не напрямую, а через **артефакты**.

---

# Artifact API — сердце совместимости

Нужен универсальный формат артефакта:

```json
{
  "artifact_id": "art_01HX...",
  "artifact_type": "ExcelProfile",
  "schema_version": "1.1.0",
  "producer": {
    "tool": "profile_excel",
    "version": "1.2.0",
    "invocation_id": "inv_01HX..."
  },
  "location": "artifacts/task_123/excel_profile.json",
  "content_hash": "sha256:...",
  "summary": "Workbook profile: 3 sheets, 2000 rows, 700 columns",
  "created_at": "2026-04-29T00:00:00Z",
  "metadata": {
    "rows": 2000,
    "columns": 700,
    "sheets": 3
  }
}
```

Модуль не должен передавать другому модулю огромный объект напрямую.

Он возвращает:

```json
{
  "artifacts": [
    {
      "artifact_id": "art_excel_profile_001",
      "artifact_type": "ExcelProfile",
      "schema_version": "1.1.0"
    }
  ]
}
```

А следующий tool получает ссылку:

```json
{
  "profile_artifact_id": "art_excel_profile_001"
}
```

Это сразу решает:

```text
- большие файлы;
- Excel на 2k×700;
- контекст модели;
- повторное использование результатов;
- дедупликацию;
- версионирование;
- audit trail.
```

---

# Tool Registry: не список функций, а каталог способностей

Registry должен хранить манифест каждого tool.

Например:

```yaml
id: tool.profile_excel
name: profile_excel
namespace: data.excel
version: 1.2.0

description: >
  Deterministically profiles an Excel workbook and produces a structured ExcelProfile artifact.

runtime:
  type: python
  entrypoint: igla_tools.excel.profile_excel:invoke

capabilities:
  - excel.profile
  - data.schema_detection
  - data.quality_summary

risk:
  level: read_only
  side_effects: false
  network: disabled

input:
  schema_ref: schemas/tools/profile_excel.input.v1.json

output:
  schema_ref: schemas/tools/profile_excel.output.v1.json

consumes:
  - artifact_type: ExcelWorkbook
    schema: "1.x"

produces:
  - artifact_type: ExcelProfile
    schema: "1.x"

policies:
  requires:
    - path_allowed
    - file_read_receipt

resources:
  timeout_seconds: 120
  max_memory_mb: 4096
  max_file_size_mb: 512

sandbox:
  profile: read_only_file_access

compatibility:
  protocol: ">=1.0,<2.0"
  backward_compatible_with:
    - "1.1.x"
    - "1.0.x"

verifier:
  type: schema_validation
  schema_ref: schemas/artifacts/excel_profile.v1.json

extensions: {}
```

Это уже нормальный “паспорт” модуля.

---

# Минимальный API Registry

Registry должен иметь примерно такие методы:

```python
class ToolRegistry:
    def register(self, manifest: ToolManifest) -> None:
        ...

    def get(self, tool_name: str, version: str | None = None) -> ToolManifest:
        ...

    def resolve(self, capability: str, constraints: dict) -> ToolManifest:
        ...

    def validate_input(self, tool_ref: ToolRef, input_data: dict) -> None:
        ...

    def validate_output(self, tool_ref: ToolRef, output_data: dict) -> None:
        ...

    def list_tools(self, filters: ToolFilter) -> list[ToolManifest]:
        ...
```

Очень важно: планировщик не должен выбирать tool по имени вслепую. Лучше так:

```text
Мне нужна capability: excel.profile
Registry подбери подходящий tool.
```

Например:

```json
{
  "capability": "excel.profile",
  "constraints": {
    "file_type": "xlsx",
    "max_rows": 5000,
    "read_only": true
  }
}
```

Registry отвечает:

```json
{
  "tool": "profile_excel",
  "version": "1.2.0"
}
```

Так потом можно заменить реализацию, не ломая мозги планировщику.

---

# Самый стабильный набор сущностей

Я бы заранее зафиксировал эти сущности:

```text
TaskSpec
Plan
PlanStep
ToolManifest
ToolInvocation
ToolResult
ArtifactDescriptor
EvidenceRecord
Receipt
PolicyDecision
RuntimeState
MemoryRecord
VerificationSpec
RollbackSpec
```

Это не значит, что всё нужно кодить сразу.
Но названия и смысл лучше зафиксировать.

---

# Базовая схема TaskSpec

```json
{
  "task_id": "task_01HX...",
  "goal": "Analyze Excel workbook and find anomalies",
  "domain": "data_analysis",

  "success_criteria": [
    "Workbook structure is profiled",
    "Suspicious columns are identified",
    "Anomaly report is generated"
  ],

  "constraints": {
    "no_file_mutation": true,
    "no_network": true,
    "max_runtime_minutes": 10
  },

  "allowed_actions": [
    "read_file",
    "profile_excel",
    "detect_anomalies",
    "generate_report"
  ],

  "forbidden_actions": [
    "modify_original_file",
    "upload_file",
    "execute_untrusted_macro"
  ],

  "context_policy": {
    "large_file_strategy": "profile_then_slice",
    "max_context_tokens": 32000
  }
}
```

TaskSpec — это “контракт задачи”.
Без него агент начинает плавать.

---

# Базовая схема PlanStep

```json
{
  "step_id": "step_003",
  "kind": "tool_invocation",

  "tool": {
    "name": "profile_excel",
    "version": "1.2.0"
  },

  "input": {
    "workbook_artifact_id": "art_workbook_001"
  },

  "depends_on": [
    "step_001",
    "step_002"
  ],

  "requires": [
    "file_read_receipt",
    "todo_checked"
  ],

  "expected_outputs": [
    {
      "artifact_type": "ExcelProfile",
      "schema_version": "1.x"
    }
  ],

  "verification": {
    "type": "artifact_schema",
    "artifact_type": "ExcelProfile"
  },

  "on_failure": {
    "mode": "enter_diagnosis",
    "allowed_next_actions": [
      "inspect_error",
      "read_logs",
      "search_memory",
      "search_docs"
    ]
  }
}
```

Именно `depends_on` решает твою идею:

```text
нельзя выполнить следующее действие до результата предыдущего
```

Это не промпт. Это DAG.

---

# Policy API

Policy Engine должен принимать не “команду”, а структурированный request:

```python
class PolicyEngine:
    def check(
        self,
        action: ActionRequest,
        state: RuntimeState,
        task: TaskSpec,
    ) -> PolicyDecision:
        ...
```

Где `PolicyDecision`:

```json
{
  "decision": "deny",
  "reason_code": "MISSING_FILE_READ_RECEIPT",
  "message": "File cannot be patched before it has been read in this task",
  "missing_requirements": [
    "file_read_receipt:/workspace/server.py"
  ],
  "allowed_next_actions": [
    "read_file"
  ]
}
```

Варианты решения:

```text
allow
deny
needs_approval
needs_diagnosis
needs_more_context
needs_memory_search
needs_documentation_search
```

Это лучше, чем просто `True/False`.

---

# Обязательные политики ядра

Я бы сразу заложил такие policy predicates:

```text
path_allowed
file_read_before_write
file_hash_not_stale
backup_before_mutation
todo_checked_before_project_mutation
previous_step_completed
no_blind_retry_after_failure
evidence_required_for_claim
verifier_required_for_mutation
timeout_required_for_execution
no_network_unless_declared
artifact_schema_valid
memory_dedupe_before_write
source_event_required_for_memory
large_artifact_not_sent_to_llm
```

Каждая policy — маленькая функция.

Например:

```python
def file_read_before_write(action, state) -> PolicyDecision:
    if action.kind != "patch_file":
        return allow()

    path = action.input["path"]
    receipt = state.read_receipts.get(path)

    if receipt is None:
        return deny(
            reason_code="MISSING_FILE_READ_RECEIPT",
            message=f"{path} was not read before patch",
            allowed_next_actions=["read_file"],
        )

    return allow()
```

Так ты получаешь “стены”, а не “надежды”.

---

# Event API

Event Store должен быть append-only.

Каждое действие, отказ, ошибка, результат — событие.

```json
{
  "event_id": "evt_01HX...",
  "task_id": "task_01HX...",
  "step_id": "step_003",
  "type": "tool_invocation_completed",
  "timestamp": "2026-04-29T00:00:00Z",

  "actor": "runtime",
  "tool": "profile_excel",

  "input_hash": "sha256:...",
  "output_hash": "sha256:...",

  "status": "success",

  "artifacts": [
    "art_excel_profile_001"
  ],

  "evidence": [
    "ev_profile_excel_001"
  ]
}
```

Почему это важно:

```text
- можно восстановить ход задачи;
- можно доказать, почему был сделан вывод;
- можно делать память из event log;
- можно дебажить ИГЛУ;
- можно запрещать повтор без диагностики;
- можно строить отчеты.
```

---

# Evidence API

Любое утверждение должно быть связано с evidence.

```json
{
  "evidence_id": "ev_01HX...",
  "type": "command_output",
  "source_event_id": "evt_01HX...",
  "summary": "pytest failed with ImportError in test_config.py",
  "location": "artifacts/task_123/command_outputs/pytest_001.txt",
  "hash": "sha256:...",
  "confidence": "high"
}
```

Потом claim:

```json
{
  "claim": "The project currently fails because config.py imports a missing symbol",
  "confidence": "high",
  "evidence_refs": [
    "ev_01HX..."
  ]
}
```

Если evidence refs пустые — claim нельзя записывать в память как факт.

---

# Memory API

Memory лучше делать не как “записать текст”, а как pipeline:

```python
class MemoryService:
    def propose(self, candidate: MemoryCandidate) -> MemoryDecision:
        ...

    def dedupe(self, candidate: MemoryCandidate) -> DedupeResult:
        ...

    def commit(self, candidate: MemoryCandidate) -> MemoryRecord:
        ...

    def retrieve(self, query: MemoryQuery) -> list[MemoryRecord]:
        ...

    def archive(self, record_id: str, reason: str) -> None:
        ...
```

MemoryCandidate:

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
  "source_events": [
    "evt_001",
    "evt_002"
  ],
  "confidence": "high"
}
```

MemoryRecord:

```json
{
  "memory_id": "mem_01HX...",
  "type": "case",
  "scope": "project:nvidia-workbench-vllm",
  "canonical_id": "case_vllm_enable_ui",
  "dedupe_key": "vllm:cli:unrecognized-argument:enable-ui",
  "status": "verified",
  "created_at": "...",
  "updated_at": "...",
  "source_events": [],
  "supersedes": [],
  "superseded_by": null
}
```

---

# Версионирование: как не переписывать API

Нужно ввести правила сразу.

## 1. Protocol version

Это версия общего envelope:

```json
"protocol_version": "1.0"
```

Менять редко.

## 2. Tool version

Это версия реализации tool:

```json
"tool": {
  "name": "profile_excel",
  "version": "1.2.0"
}
```

## 3. Input schema version

Это версия входных параметров tool.

## 4. Output schema version

Это версия output tool.

## 5. Artifact schema version

Это версия артефакта.

---

# Правила совместимости

Я бы зафиксировал:

```text
Можно в minor-версии:
- добавлять optional поля;
- добавлять новые artifact metadata;
- добавлять новые metrics;
- добавлять новые warnings.

Нельзя без major-версии:
- удалять поле;
- переименовывать поле;
- менять смысл поля;
- менять тип поля;
- делать optional поле required;
- менять artifact_type.
```

Пример:

```text
ExcelProfile v1.1 совместим с v1.0.
ExcelProfile v2.0 требует adapter.
```

---

# Adapter API

Если новый модуль производит `ExcelProfile v2`, а старый потребляет `ExcelProfile v1`, Runtime должен иметь адаптеры:

```python
class ArtifactAdapter:
    def can_convert(self, from_type, from_version, to_type, to_version) -> bool:
        ...

    def convert(self, artifact_id: str, target_version: str) -> str:
        ...
```

Пример:

```text
ExcelProfile v2.0 → ExcelProfile v1.1
```

Это сильно снижает будущую боль.

---

# Не делай “полную совместимость всех модулей со всеми”

Это ловушка.

Не нужно:

```text
любой модуль совместим с любым модулем
```

Нужно:

```text
любой модуль совместим с ядром;
модули совместимы друг с другом через artifact schemas.
```

То есть:

```text
Module A → Artifact X
Module B consumes Artifact X
```

Если artifact schema совпадает — совместимы.
Если нет — нужен adapter.
Если adapter нет — Registry не разрешает связку.

---

# Как должен выглядеть вызов Excel-пайплайна

Пусть пользователь дал Excel.

## Step 1

```json
{
  "tool": {
    "name": "register_file_artifact",
    "version": "1.0.0"
  },
  "input": {
    "path": "data/report.xlsx",
    "artifact_type": "ExcelWorkbook"
  }
}
```

Output:

```json
{
  "artifacts": [
    {
      "artifact_id": "art_workbook_001",
      "artifact_type": "ExcelWorkbook",
      "schema_version": "1.0.0"
    }
  ]
}
```

## Step 2

```json
{
  "tool": {
    "name": "profile_excel",
    "version": "1.2.0"
  },
  "input": {
    "workbook_artifact_id": "art_workbook_001",
    "profile_level": "full",
    "include_samples": true,
    "sample_rows_per_sheet": 20
  }
}
```

Output:

```json
{
  "artifacts": [
    {
      "artifact_id": "art_excel_profile_001",
      "artifact_type": "ExcelProfile",
      "schema_version": "1.1.0"
    }
  ]
}
```

## Step 3

```json
{
  "tool": {
    "name": "detect_excel_anomalies",
    "version": "1.0.0"
  },
  "input": {
    "profile_artifact_id": "art_excel_profile_001",
    "checks": [
      "missingness",
      "duplicates",
      "outliers",
      "type_mismatch",
      "formula_errors"
    ]
  }
}
```

Output:

```json
{
  "artifacts": [
    {
      "artifact_id": "art_anomaly_report_001",
      "artifact_type": "ExcelAnomalyReport",
      "schema_version": "1.0.0"
    }
  ]
}
```

LLM получает не таблицу, а:

```text
ExcelProfile summary
AnomalyReport summary
targeted slices by request
```

---

# Многопараметровость лучше держать в input schema

То есть у каждого tool свой input, но через общий envelope.

Например, `profile_excel.input.v1.json`:

```json
{
  "type": "object",
  "required": [
    "workbook_artifact_id"
  ],
  "properties": {
    "workbook_artifact_id": {
      "type": "string"
    },
    "sheet_selector": {
      "type": "object",
      "properties": {
        "mode": {
          "type": "string",
          "enum": ["all", "include", "exclude"]
        },
        "names": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      }
    },
    "profile_level": {
      "type": "string",
      "enum": ["basic", "standard", "full"],
      "default": "standard"
    },
    "include_samples": {
      "type": "boolean",
      "default": true
    },
    "sample_rows_per_sheet": {
      "type": "integer",
      "minimum": 0,
      "maximum": 100,
      "default": 20
    }
  },
  "additionalProperties": false
}
```

Так ты можешь менять параметры конкретного tool, не ломая Kernel API.

---

# Где держать “extensions”

В каждом envelope можно иметь:

```json
"extensions": {}
```

Но правило:

```text
extensions не могут быть обязательными для корректной работы базового протокола.
```

То есть extensions — для будущих возможностей, экспериментов, plugin-specific metadata.

Не надо пихать туда критичную логику.

---

# Что должно быть в Kernel API

Минимальный набор сервисов:

```text
TaskService
PlanService
PolicyEngine
ToolRegistry
Executor
ArtifactStore
EvidenceStore
EventStore
MemoryService
ContextManager
VerifierService
RollbackManager
```

Но не обязательно всё кодить сразу.

Важно, чтобы они были **разделены концептуально**.

---

# Стабильные интерфейсы сервисов

## ArtifactStore

```python
class ArtifactStore:
    def put(self, artifact: ArtifactCreateRequest) -> ArtifactDescriptor:
        ...

    def get(self, artifact_id: str) -> ArtifactDescriptor:
        ...

    def open(self, artifact_id: str) -> bytes:
        ...

    def derive(
        self,
        parent_ids: list[str],
        artifact: ArtifactCreateRequest,
    ) -> ArtifactDescriptor:
        ...
```

## EventStore

```python
class EventStore:
    def append(self, event: EventRecord) -> EventRecord:
        ...

    def list_by_task(self, task_id: str) -> list[EventRecord]:
        ...
```

## EvidenceStore

```python
class EvidenceStore:
    def create(self, evidence: EvidenceCreateRequest) -> EvidenceRecord:
        ...

    def get(self, evidence_id: str) -> EvidenceRecord:
        ...
```

## Executor

```python
class Executor:
    def invoke(self, invocation: ToolInvocation) -> ToolResult:
        ...
```

## PolicyEngine

```python
class PolicyEngine:
    def check_invocation(
        self,
        invocation: ToolInvocation,
        state: RuntimeState,
    ) -> PolicyDecision:
        ...
```

---

# Как избежать переписывания tool API

Самые важные правила:

## 1. Tool принимает один объект

Не набор аргументов.

Плохо:

```python
def invoke(path, mode, context, timeout, memory, policy):
    ...
```

Хорошо:

```python
def invoke(invocation: ToolInvocation) -> ToolResult:
    ...
```

---

## 2. Tool не получает прямой доступ к ядру

Плохо:

```python
def invoke(kernel, memory, registry, executor):
    ...
```

Хорошо:

```python
def invoke(invocation):
    # работает только с input/context/artifacts,
    # возвращает result
```

Если tool нужно что-то прочитать, он должен получить artifact или разрешенный path от Runtime.

---

## 3. Tool не вызывает другие tools

Плохо:

```python
def profile_and_analyze_excel():
    profile = profile_excel()
    anomalies = detect_anomalies(profile)
```

Хорошо:

```text
Pipeline собирает Orchestrator.
Tool делает только одну функцию.
```

---

## 4. Всё большое — через ArtifactStore

Не передавать огромные данные в JSON.

```text
JSON: metadata + artifact_id
Файл: реальные данные
```

---

## 5. Все side effects — только через Kernel

Модуль не должен сам писать куда попало.

Для mutating tools:

```text
Tool предлагает patch artifact.
Kernel применяет patch после policy check.
```

Это особенно важно для безопасности.

---

# Отдельный важный момент: patch как артефакт

В идеале модуль не должен сам менять файл.

Лучше так:

```text
generate_patch → PatchArtifact
apply_patch → mutating kernel tool
```

То есть:

## Tool генерирует патч

```json
{
  "artifact_type": "FilePatch",
  "schema_version": "1.0.0",
  "target_path": "server.py",
  "base_sha256": "abc123",
  "diff": "..."
}
```

## Kernel применяет патч

Перед применением проверяет:

```text
- файл был прочитан;
- base_sha256 совпадает;
- backup создан;
- TODO checked;
- verifier declared;
- rollback possible.
```

Это намного безопаснее, чем давать каждому tool право писать файлы.

---

# Для кода модулей: два типа tools

Я бы разделил tools на два класса.

## Pure tools

Без side effects.

```text
parse_log
profile_excel
detect_anomalies
generate_patch
summarize_document
classify_error
```

Они безопаснее.

## Effect tools

Меняют мир.

```text
apply_patch
run_command
install_package
restart_container
modify_firewall
write_memory
```

Effect tools должны быть в ядре или под очень жесткой policy.

Это значит:

```text
AI-generated tools могут быть только pure tools на первом этапе.
```

А effect tools пишешь сам.

---

# Как встроить “нельзя дальше без результата”

В PlanStep:

```json
{
  "step_id": "step_2",
  "depends_on": ["step_1"]
}
```

Runtime:

```python
def can_run_step(step, state):
    for dep in step.depends_on:
        if state.steps[dep].status != "completed":
            return deny("DEPENDENCY_NOT_COMPLETED")
    return allow()
```

То есть это вообще не вопрос LLM.

---

# Как встроить “после ошибки иди в память/доки”

В state:

```json
{
  "mode": "failure_diagnosis_required",
  "last_error": {
    "kind": "ToolFailed",
    "code": "UNRECOGNIZED_ARGUMENT",
    "source_step": "step_4"
  },
  "required_before_retry": [
    "classify_error",
    "search_memory_or_docs",
    "produce_new_hypothesis"
  ]
}
```

Policy запрещает:

```text
patch_file
rerun_same_command
install_package
final_answer_success
```

Разрешает:

```text
read_logs
search_memory
search_docs
inspect_help
classify_error
```

Это опять state machine.

---

# Как встроить дедупликацию

Память не должна быть tool’ом, который просто пишет Markdown.

Должен быть `MemoryCandidate`:

```json
{
  "type": "fact",
  "scope": "host:spark-gb10",
  "subject": "torch",
  "predicate": "version",
  "object": "2.10.0+cu130",
  "dedupe_key": "host:spark-gb10:torch:version",
  "source_events": ["evt_123"],
  "confidence": "observed"
}
```

MemoryService:

```text
1. canonical key check
2. source hash check
3. semantic similarity check
4. conflict check
5. commit or merge
```

Если дубль:

```json
{
  "decision": "merge",
  "existing_memory_id": "mem_001",
  "action": "add_source_event"
}
```

Не новая память, а новый source к старой памяти.

---

# Как сделать Obsidian без боли

Obsidian должен быть только mirror.

Canonical memory:

```text
SQLite/Postgres/JSONL
```

Mirror:

```text
Obsidian Markdown
```

То есть:

```text
MemoryRecord → Markdown note
```

Но не наоборот как основной источник.

Можно сделать экспорт:

```text
memories/cases/*.md
memories/facts/*.md
```

Фронтматтер:

```yaml
---
memory_id: mem_001
type: case
scope: project:nvidia-workbench-vllm
dedupe_key: vllm:cli:unrecognized-argument:enable-ui
status: verified
confidence: high
source_events:
  - evt_123
  - evt_124
---
```

Если ты вручную поменял Obsidian note, это должно идти через import pipeline, а не напрямую менять canonical memory.

---

# Что реально стоит “заморозить” заранее

Я бы заморозил вот это:

```text
1. ToolInvocation envelope
2. ToolResult envelope
3. ToolManifest format
4. ArtifactDescriptor format
5. EventRecord format
6. EvidenceRecord format
7. PolicyDecision format
8. MemoryCandidate/MemoryRecord format
```

А вот это можно менять:

```text
1. конкретные tools;
2. конкретные input schemas;
3. конкретные output schemas;
4. конкретные artifact types;
5. конкретные memory categories;
6. конкретные policy rules.
```

---

# Самый первый “идеальный” API v0.1

Я бы начал с такого набора файлов:

```text
igla_protocol/
├── protocol.py
├── manifest.py
├── invocation.py
├── result.py
├── artifact.py
├── event.py
├── evidence.py
├── policy.py
├── memory.py
└── versioning.py
```

И сделал Pydantic-модели.

Примерно:

```python
from typing import Any, Literal
from pydantic import BaseModel, Field


class ToolRef(BaseModel):
    name: str
    version: str


class ToolInvocation(BaseModel):
    protocol_version: str = "1.0"
    invocation_id: str
    task_id: str
    step_id: str | None = None

    tool: ToolRef
    input: dict[str, Any] = Field(default_factory=dict)

    context: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    kind: str
    code: str
    message: str
    retryable: bool = False
    requires_diagnosis: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactDescriptor(BaseModel):
    artifact_id: str
    artifact_type: str
    schema_version: str
    location: str | None = None
    content_hash: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    evidence_id: str
    kind: str
    summary: str | None = None


class ToolResult(BaseModel):
    protocol_version: str = "1.0"
    invocation_id: str
    task_id: str
    step_id: str | None = None

    status: Literal["success", "failed", "blocked", "partial"]

    output: dict[str, Any] = Field(default_factory=dict)

    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    error: ToolError | None = None
    next_hints: list[dict[str, Any]] = Field(default_factory=list)
```

Это уже можно не переписывать долго.

---

# Главное правило проектирования API

**Не расширяй сигнатуры функций. Расширяй схемы данных.**

Плохо:

```python
def run_tool(path, mode, user, memory, policy, context, receipts, artifacts):
    ...
```

Хорошо:

```python
def run_tool(invocation: ToolInvocation) -> ToolResult:
    ...
```

Потом тебе нужно добавить `approval_context`?

Не меняешь сигнатуру.
Добавляешь поле:

```json
"context": {
  "approval_context": {}
}
```

Нужно добавить resource limits?

```json
"policy": {
  "max_memory_mb": 4096
}
```

Нужно добавить dependency evidence?

```json
"dependencies": {
  "required_evidence": []
}
```

Сигнатура не меняется.

---

# Какая часть должна быть “выдрочена до идеала”

Не весь проект.

До идеала надо довести **границы**:

```text
ToolInvocation
ToolResult
ToolManifest
ArtifactDescriptor
PolicyDecision
EventRecord
```

А реализации можно менять.

Это как системный ABI.
Если ABI стабильный — внутренности можно переписывать хоть 100 раз.

---

# Практический вывод

Чтобы не переписывать API модулей по 10 раз, делай так:

```text
1. Каждый tool принимает один ToolInvocation.
2. Каждый tool возвращает один ToolResult.
3. Модули не вызывают друг друга напрямую.
4. Модули связываются только через ArtifactStore.
5. Registry хранит capabilities, schemas, risks, policies.
6. Policy Engine решает, можно ли вызвать tool.
7. Все изменения мира идут через kernel effect tools.
8. Большие данные не идут в LLM-контекст, только artifact refs/summaries.
9. Память пишется только через MemoryCandidate → dedupe → commit.
10. Версии есть у protocol, tool, schema и artifact.
```

Если это заложить сразу, то потом можно спокойно добавлять:

```text
Excel tools
Docker tools
Git tools
vLLM tools
Quickemu tools
Obsidian mirror
generated tools
Rust executor
web GUI
локальные модели
```

без переписывания базового API.

Самая короткая формула:

```text
Стабильным должен быть не модуль.
Стабильным должен быть протокол между ядром и модулем.
```

Именно его надо проектировать заранее.
