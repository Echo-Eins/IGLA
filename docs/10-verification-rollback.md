# 10. Verifier, Evidence, обратимость

## 1. Зачем

Без верификатора ИГЛА — это «уверенно болтающий ассистент». С верификатором она становится инженерной системой: каждое заявление подкреплено evidence, каждое изменение — проверено, каждое неудачное действие — откатано.

Формула:

```
LLM без verifier         = болтун.
LLM + tools              = агент.
LLM + tools + verifier
     + rollback + memory = ИГЛА.
```

## 2. Evidence

`EvidenceRecord`:

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

Виды:

```
command_output       stdout/stderr/exit_code конкретной команды
file_excerpt         содержимое файла или его секция
log_excerpt          выдержка из лога с границами
artifact_metadata    метаданные артефакта (размеры, типы, хеш)
memory_lookup        результат retrieval из Memory
documentation        выдержка из официальной документации
verifier_output      результат конкретного verifier check
external             веб/issues; помечается отдельно
```

Любое утверждение в финальном отчёте имеет `evidence_refs`:

```json
{
  "claim": "vLLM does not support --enable-ui in this environment",
  "confidence": "high",
  "evidence_refs": ["ev_01HX_help_output", "ev_01HX_error_log"],
  "scope": "container:workbench-vllm"
}
```

Без evidence claim **не** попадает ни в отчёт, ни в Memory.

## 3. Verifier: общая модель

`VerificationSpec` объявляется в каждом mutating step:

```json
{
  "type": "composite",
  "checks": [
    {"type": "schema_validation", "artifact_type": "ExcelProfile"},
    {"type": "command", "cmd": "vllm serve --help", "expect": {"exit_code": 0}},
    {"type": "http", "url": "http://127.0.0.1:8000/v1/models", "expect": {"status": 200, "body_contains": "gpt-oss-20b"}},
    {"type": "process_alive", "name": "vllm"},
    {"type": "no_regressions", "test_suite": "pytest"}
  ],
  "policy": "all_must_pass"
}
```

`VerifierService` запускает каждый check в sandbox, собирает evidence, выставляет статус:

```
verifier.status = passed | failed | inconclusive
```

`inconclusive` — если по объективным причинам нельзя доказать ни успех, ни провал (например, требуется ресурс, который недоступен). В этом случае task переходит в `BLOCKED`, не в `COMPLETED`.

## 4. Типовые verifier для целевых доменов

### Для кода

```
pytest passed
mypy passed
ruff passed
program exits with 0
git diff соответствует объявленному PatchPlan
```

### Для vLLM

```
process is alive
port is open (8000/etc)
GET /v1/models returns expected model id
test completion works (короткий prompt → ответ)
VRAM usage в пределах профиля
```

### Для Quickemu

```
config validates (quickemu --check / эквивалент)
OVMF path exists
disk image exists
QEMU starts (тестовый таймаутный boot)
monitor socket appears
VM process alive в течение N секунд
```

### Для nftables killswitch

```
nft -c проходит
ruleset содержит ожидаемый chain
без tun0 трафик блокируется (через тестовый sandbox)
с tun0 трафик допускается
cleanup восстанавливает baseline ruleset
```

### Для Excel/таблиц

```
ExcelProfile schema validation
число строк/листов соответствует исходному
аномалии reproducible (повторный прогон даёт тот же hash)
```

## 5. Обратимость по умолчанию

Любая мутация проходит цикл:

```
before snapshot
    ↓
action
    ↓
verification
    ↓
commit (если verifier passed)
    ↓
rollback (если failed/inconclusive)
```

### Файлы

```
backup → edit → diff → test → keep | revert
```

### Git-проекты

```
branch → patch → tests → diff → commit suggestion
```

(commit/push делает только пользователь либо отдельный effect tool с approval).

### Docker

```
inspect → create test container → run → verify → replace
```

### Системные изменения (firewall/systemd/конфиги)

```
dry-run (если возможно)
backup config
apply
healthcheck
auto-rollback по таймеру, если healthcheck не подтверждён
```

`auto-rollback по таймеру` — особенно важен для killswitch/firewall: если в течение N секунд после применения связь не подтверждена, runtime откатывает изменение, не дожидаясь команды.

## 6. RollbackManager

API:

```python
class RollbackManager:
    def snapshot(self, target: SnapshotRequest) -> SnapshotDescriptor: ...
    def plan(self, action: ActionRequest) -> RollbackPlan: ...
    def execute(self, plan_id: str) -> RollbackResult: ...
    def discard(self, plan_id: str) -> None: ...
```

`RollbackPlan` декларируется до `ToolInvocation` и привязан к `step_id`:

```json
{
  "plan_id": "rbp_01HX...",
  "step_id": "step_007",
  "strategy": "restore_backup",
  "snapshots": ["snap_file_001"],
  "post_actions": ["healthcheck_http"],
  "auto_timeout_seconds": 30
}
```

Если verifier `passed` — `discard`. Если `failed`/`inconclusive` — `execute`. Любая попытка mutation без `RollbackPlan` (для tools, которые требуют его в манифесте) — отказ Policy Engine.

## 7. Policy ↔ Verifier ↔ Rollback

Связка инвариантов:

- `reversible_by_default`: mutating action **обязано** иметь `RollbackPlan` или объявить exception (с записью в EventStore + approval).
- `verifier_required_for_mutation`: каждая мутация имеет `VerificationSpec`.
- `evidence_required`: если мутация заявлена успешной, в `evidence` лежит результат verifier.

Эти три правила — каркас «инженерности» ИГЛЫ.

## 8. Финальный отчёт

`Report Builder` собирает:

```
1. Цель
2. TaskSpec и критерии
3. Timeline шагов (со ссылками на ToolInvocation/Result)
4. Verified facts (claim → evidence_refs)
5. Unverified assumptions (если остались)
6. Откатанные шаги (с причиной)
7. Открытые вопросы
8. Сохранённые в Memory кейсы
9. Рекомендации / next steps
```

Без evidence_refs пункт «Verified facts» пуст. Без verifier-результата пункт «Timeline» помечает шаг как `inconclusive`.

## 9. Поведение при невозможности verify

Если для шага не существует автоматического verifier’а (не во всех доменах он возможен):

- шаг отмечается как `requires_human_verification`;
- задача переходит в `NEEDS_USER_APPROVAL` после исполнения;
- пользователь явно подтверждает успех в UI;
- runtime фиксирует evidence типа `human_attestation` с ID пользователя и timestamp.

«Человек подтвердил» тоже считается evidence, но с пометкой `confidence: external` и `audit_level: full`.

## 10. Резюме

Verifier и Rollback — два конца одной палки:

- verifier доказывает успех;
- rollback страхует от провала;
- evidence связывает оба с реальными артефактами;
- policy физически не пускает мутацию без обоих.

Это и есть «инженерная среда»: ИГЛА не «надеется», что всё хорошо — она проверяет и при необходимости откатывает.
