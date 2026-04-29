# 03. Kernel, политики, state machine

## 1. Зачем нужен отдельный Kernel

Orchestrator/LLM по природе нестабильны: они могут «забыть» прочитать файл, повторить упавшую команду, сослаться на придуманный факт. Чтобы это не разрушало систему, есть **Kernel** — детерминированный набор сервисов, в которых не работает «модель уверена, что всё хорошо». Работают только проверки на данных.

Главная формула:

```
LLM proposes. Runtime disposes.
```

Любое действие модели проходит через Kernel и либо одобряется, либо отклоняется со структурированной причиной и списком разрешённых следующих действий.

## 2. Конституция (constitution)

Файл: `igla/policies/constitution.yaml`. Это не философия, а **проверяемые** инварианты. Каждое правило сопровождается реализующим предикатом в коде.

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

  - id: memory_dedupe_required
    rule: "Memory writes must include source_event and pass dedupe checks."

  - id: schema_validated
    rule: "Every ToolInvocation/ToolResult must validate against its declared schema."

  - id: sandbox_required
    rule: "Execution tools must run inside a declared sandbox profile."

  - id: no_unapproved_generated_tool
    rule: "AI-generated tools cannot be registered without passing the quarantine pipeline."
```

Конституция не меняется в одной сессии. Изменения — только через bump `policy_version` и migration-план.

## 3. Action Gate Matrix

Любое действие — это набор предикатов, а не «можно/нельзя».

```
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

Action: send_to_llm_context
Requires:
  - artifact_size_within_budget
  - large_artifact_indexed_or_profiled
```

Каждый предикат — маленькая функция. Минимальный пример:

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

    if state.current_hash(path) != receipt.sha256:
        return deny(
            reason_code="STALE_FILE_READ_RECEIPT",
            message="File hash changed after read; re-read required",
            allowed_next_actions=["read_file"],
        )

    return allow()
```

## 4. Policy Engine API

```python
class PolicyEngine:
    def check_invocation(
        self,
        invocation: ToolInvocation,
        state: RuntimeState,
        task: TaskSpec,
    ) -> PolicyDecision: ...

    def explain(self, decision: PolicyDecision) -> str: ...
```

`PolicyDecision`:

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
  ],
  "forbidden_next_actions": [
    "write_file_without_patch",
    "rerun_same_command"
  ],
  "needs_human_approval": false
}
```

Возможные значения `decision`:

```
allow
deny
needs_approval
needs_diagnosis
needs_more_context
needs_memory_search
needs_documentation_search
```

## 5. State Machine

Каждый шаг плана и каждая задача проходят детерминированный автомат.

### Статусы шага

```
PROPOSED        — LLM/планировщик предложил
APPROVED        — прошёл policy
RUNNING         — исполняется
COMPLETED       — успех + verifier OK
FAILED          — провал (включая verifier failure)
BLOCKED         — зависимости не выполнены / approval не получен
ROLLED_BACK     — откат после неудачной попытки
SKIPPED         — лишний после перепланирования
```

Шаг не может перейти в `RUNNING`, если все его `depends_on` не в `COMPLETED`.

### Режимы задачи

```
READY                          — можно планировать/исполнять очередной шаг
WAITING_FOR_TOOL_RESULT        — ожидание ToolResult
FAILURE_DIAGNOSIS_REQUIRED     — после ошибки; запрещён повтор
NEEDS_USER_APPROVAL            — критический шаг ждёт пользователя
NEEDS_USER_CLARIFICATION       — task spec неполон/конфликт с TODO
BLOCKED                        — не может двигаться (нет tool, ресурса, и т.п.)
TASK_DONE                      — финальный отчёт сформирован
ARCHIVED                       — задача закрыта и помещена в архив
```

В режиме `FAILURE_DIAGNOSIS_REQUIRED`:

- запрещены: `patch_file`, `apply_patch`, `run_command` с прежними аргументами, `install_package`, `final_answer_success`;
- разрешены: `read_logs`, `inspect_help`, `inspect_state`, `search_memory`, `search_docs`, `classify_error`, `propose_minimal_experiment`, `ask_user_if_blocked`.

Выход из режима возможен только при наличии `failure_classified == true`, нового `MemoryCandidate` либо найденной документации, и `changed_condition_declared`.

## 6. ReceiptManager

Каждое чтение файла рождает квитанцию:

```json
{
  "type": "FileReadReceipt",
  "receipt_id": "rcp_01HX...",
  "path": "/project/server.py",
  "sha256": "abc123...",
  "bytes_read": 18342,
  "read_mode": "full",
  "task_id": "task_001",
  "timestamp": "2026-04-29T00:00:00Z"
}
```

Любая мутация ссылается на receipt. Если содержимое файла изменилось:

```
DENY: stale_read_receipt
Reason: file hash changed after read. Re-read required.
```

ReceiptManager также ведёт `intake receipts` (TODO/README/docs прочитаны) и `evidence receipts` (логи/выводы команд приобщены к делу).

## 7. EventStore

Append-only журнал. JSONL на старте, SQLite/Postgres позже.

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
  "artifacts": ["art_excel_profile_001"],
  "evidence": ["ev_profile_excel_001"]
}
```

Подмножества событий:

```
task_created            plan_proposed             plan_approved
step_proposed           step_approved             step_started
step_completed          step_failed               step_rolled_back
tool_invocation_*       policy_decision           memory_candidate_*
verifier_*              user_approval_*           runtime_mode_changed
```

## 8. Verifier и Rollback

См. [10-verification-rollback.md](10-verification-rollback.md). На уровне Kernel:

- каждый mutating step обязан декларировать `verification` и `on_failure.rollback`;
- VerifierService запускает критерии и присваивает `step_status`;
- RollbackManager хранит снапшоты и применяет план отката, если verifier провален.

## 9. Allowed Next Actions

После каждого шага runtime возвращает модели не «делай что хочешь», а строгий список:

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

Это снижает хаос радикально — модель не может «случайно» сделать что-то опасное, потому что физически нет такого варианта в meню.

## 10. Реакции на нарушения

Если модель присылает невалидный или запрещённый proposal, Kernel возвращает структурированный отказ:

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

Модель должна не «спорить», а выбрать одно из allowed actions. Это часть её мотивации/ограничений (см. [08-llm-planner.md](08-llm-planner.md)).

## 11. Где Rust

Поздние стадии разработки выносят в Rust то, где «Kernel не должен ошибаться»:

- `policy_guard` — проверка предикатов, классификация команд, blacklist токенов;
- `process_supervisor` — запуск и контроль дочерних процессов, timeout/kill, ресурсы;
- `sandbox_runner` — обвязка над Docker/firejail/nsjail/bwrap;
- `log_parser` — быстрый парсер логов;
- `policy_kernel` — ядро инвариантов с предсказуемой производительностью.

Подключаются через PyO3. Python-часть ядра остаётся, но критические инварианты переезжают в Rust по мере стабилизации.
