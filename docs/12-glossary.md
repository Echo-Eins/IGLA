# 12. Глоссарий

Глоссарий охватывает термины, которые встречаются в документации. Если термин в документе используется в специфическом смысле, он есть здесь.

---

**ИГЛА** — Итеративно-Генерируемый Локальный Анализ. Локальная инженерная среда, в которой LLM выступает заменяемым планировщиком, а реальные действия совершает Runtime под политиками.

**Action Gate Matrix** — таблица предикатов, которым должен удовлетворять каждый класс действий перед исполнением (см. [03-kernel-and-policies.md](03-kernel-and-policies.md)).

**Allowed Next Actions / Forbidden Next Actions** — структурированный список того, что модель может/не может предложить на следующем шаге. Формируется PolicyEngine.

**Artifact / ArtifactDescriptor** — типизированная единица данных, которой обмениваются tools. Адресуется по `artifact_id`, имеет `artifact_type`, `schema_version`, `content_hash`.

**ArtifactStore** — сервис хранения артефактов; immutable; поддерживает производные артефакты (`derive`).

**Backup** — снапшот файла/конфига до мутации, на который ссылается RollbackPlan.

**Capability** — абстрактная способность (например, `excel.profile`, `quickemu.config.validate`). Tool Registry разрешает запрос по capability+constraints, а не по имени.

**Case Memory** — записи о решённых проблемах: симптом → причина → фикс → проверка.

**Checkpoint (state checkpoint)** — компактный снимок состояния задачи, позволяющий продолжить работу с «чистым» контекстом без потери прогресса.

**Constitution** — `igla/policies/constitution.yaml`; набор глобальных инвариантов системы.

**ContextManager** — сервис, который определяет, что попадает в контекст LLM, по уровням L0–L5 и бюджетам.

**Context Pack** — рабочий пакет контекста для конкретного запроса к модели.

**Dedupe (дедупликация)** — четырёхслойный механизм предотвращения дубликатов в памяти: canonical key, source hash, semantic, conflict.

**Effect tool** — инструмент, изменяющий мир (apply\_patch, run\_command, install\_package, restart\_container, modify\_firewall, write\_memory). Только в ядре или под жёсткой policy.

**Event Memory / EventStore** — append-only журнал всех событий runtime; основа аудита и memory distillation.

**Evidence / EvidenceRecord / evidence\_refs** — типизированные доказательства, на которые ссылаются claims.

**FailureDiagnosisRequired** — режим State Machine после провала шага; запрещены повторные мутации, разрешены diagnostic actions.

**Fact Memory** — проверенные факты о системе (версии, пути, флаги, конфиги).

**Handoff** — передача состояния задачи между моделями (например, большая → малая) через checkpoint.

**Hypothesis Engine** — компонент Orchestrator, генерирующий и ранжирующий гипотезы для диагностических задач.

**IGLA Loop** — целевой цикл: observe → hypothesize → plan → act → verify → learn → repeat.

**Intake** — этап обнаружения TODO/README/docs/issue до изменения проекта.

**Kernel (IGLA Kernel)** — детерминированное ядро: PolicyEngine, StateMachine, ToolRegistry, Executor, ArtifactStore, EvidenceStore, EventStore, VerifierService, RollbackManager, ReceiptManager, SchemaValidator.

**LLM Client** — единый интерфейс к разным моделям (большая/малая/локальная/облачная/embeddings).

**MCP** — Model Context Protocol; внешний стандарт, который ИГЛА может поддержать как адаптер, но не использует как первоисточник.

**MemoryCandidate / MemoryRecord** — кандидат на запись и итоговая запись в Memory; обязательно содержат scope, dedupe\_key, source\_events, confidence.

**MemoryConflict** — структурированный конфликт между старой и новой записью; требует явного разрешения.

**MemoryService** — сервис propose/dedupe/commit/retrieve/archive/supersede.

**Mode (runtime mode)** — статус задачи: READY, WAITING\_FOR\_TOOL\_RESULT, FAILURE\_DIAGNOSIS\_REQUIRED, NEEDS\_USER\_APPROVAL, NEEDS\_USER\_CLARIFICATION, BLOCKED, TASK\_DONE, ARCHIVED.

**Obsidian Mirror** — Markdown-зеркало Memory; не первоисточник.

**Orchestrator** — слой над Kernel: planner, intake, hypothesis engine, experiment designer, context manager, memory curator, report builder, LLM client.

**Pack (Domain Pack)** — набор tools, artifact types, verifiers и recipes под конкретный домен (vLLM, Quickemu, nftables, …).

**Plan / PlanStep** — DAG из шагов; каждый шаг типизирован, имеет `depends_on`, `requires`, `expected_outputs`, `verification`, `on_failure`.

**Policy Engine** — сервис проверки предикатов; возвращает PolicyDecision.

**PolicyDecision** — результат policy check: allow / deny / needs\_approval / needs\_diagnosis / needs\_more\_context / needs\_memory\_search / needs\_documentation\_search.

**Preference Memory** — устойчивые предпочтения пользователя.

**Project Memory** — состояние конкретных проектов.

**Proposal** — структурированное предложение модели (action + input + reason + verification\_idea).

**Pure tool** — инструмент без побочных эффектов (parse, profile, classify, generate). Допускается AI generation после quarantine.

**Quarantine** — карантинная директория `.igla/quarantine/` для AI-generated tools; см. Tool Forge.

**ReceiptManager** — сервис read/intake/evidence-квитанций; основа политик `read_before_write`, `todo_before_execution`, `evidence_required`.

**Report Builder** — собирает финальный отчёт; без evidence\_refs claims в отчёт не попадают.

**Risk Level** — Level 0…5 (см. [06-sandbox-security.md](06-sandbox-security.md)).

**Rollback / RollbackManager / RollbackPlan** — обратимость по умолчанию; план отката привязан к шагу.

**Sandbox Profile** — набор ограничений для исполнения tool: FS, network, capabilities, ресурсы.

**Scope** — обязательное поле памяти, ограничивающее применимость факта (host:..., project:..., container:..., user:..., global).

**Step Barrier** — инвариант: следующий шаг невозможен без COMPLETED-результата зависимостей.

**State Machine** — детерминированный автомат статусов шагов и режимов задачи.

**TaskSpec** — структурированный «контракт задачи»: цель, домен, риск, критерии успеха, разрешённые/запрещённые действия, политика контекста.

**Task Ledger** — постоянный объект состояния задачи: цели, шаги, гипотезы, evidence, запреты, next allowed action.

**Tool / ToolInvocation / ToolResult / ToolManifest / ToolRegistry / ToolRef / ToolError** — см. [04-tool-protocol.md](04-tool-protocol.md).

**Tool Forge** — карантинный конвейер генерации новых tools; см. [09-tool-forge.md](09-tool-forge.md).

**Verifier / VerificationSpec / VerifierService** — проверки успеха mutating-шагов; см. [10-verification-rollback.md](10-verification-rollback.md).

**Working Context** — временная память текущей задачи (active facts, files, evidence, budgets, open questions).

**Workspace** — каталог, в пределах которого ИГЛА имеет права; всё остальное — `forbidden_paths` без явной policy override.

---

Сокращения, встречающиеся по тексту:

```
DAG     directed acyclic graph
FS      filesystem
FTS     full-text search
LLM     large language model
MCP     Model Context Protocol
PII     personally identifiable information
PR      pull request
RPC     remote procedure call
SRE     site reliability engineer
TTL     time to live
UDS     unix domain socket
```
