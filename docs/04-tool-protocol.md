# 04. Протокол инструментов и артефактов

## 1. Главная идея

Стабильным должен быть **протокол**, а не модули. Каждый tool принимает один `ToolInvocation` и возвращает один `ToolResult`. Между tools «не существует» прямого вызова: они общаются через **артефакты** в `ArtifactStore`.

```
Kernel → ToolInvocation → Tool
Tool   → ToolResult     → Kernel
```

Tool не имеет доступа к ядру, памяти, реестру или другим tools напрямую. Он работает только с тем, что пришло в envelope, и тем, что Kernel разрешил трогать.

## 2. ToolInvocation (envelope)

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

  "input": {
    "workbook_artifact_id": "art_workbook_001",
    "profile_level": "full",
    "include_samples": true,
    "sample_rows_per_sheet": 20
  },

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
    "required_receipts": ["rcp_file_001"],
    "required_artifacts": ["art_workbook_001"],
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

Сигнатура tool:

```python
def invoke(invocation: ToolInvocation) -> ToolResult: ...
```

Никаких лишних аргументов. Расширение делается через схему `input` или `extensions`, не через сигнатуру.

## 3. ToolResult (envelope)

```json
{
  "protocol_version": "1.0",
  "invocation_id": "inv_01HX...",
  "task_id": "task_01HX...",
  "step_id": "step_003",

  "status": "success",

  "output": {
    "summary": "Workbook profile generated"
  },

  "artifacts": [
    {
      "artifact_id": "art_excel_profile_001",
      "artifact_type": "ExcelProfile",
      "schema_version": "1.1.0"
    }
  ],

  "evidence": [
    {
      "evidence_id": "ev_profile_excel_001",
      "kind": "tool_output",
      "summary": "Profile generated for 3 sheets, 2000 rows, 700 columns"
    }
  ],

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

При ошибке:

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

`error.kind`, `retryable`, `requires_diagnosis` управляют поведением ядра:

- можно ли повторить;
- нужно ли искать документацию;
- нужно ли искать память;
- нужно ли заблокировать дальнейшие шаги;
- можно ли идти дальше без вмешательства.

## 4. ToolManifest

Каждый tool имеет паспорт в реестре.

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

Для эффект-tool добавляются поля `rollback`, требования `backup_plan`, `verifier_plan`, `requires_approval`, `mutating_paths`.

## 5. Tool Registry

Минимальный API:

```python
class ToolRegistry:
    def register(self, manifest: ToolManifest) -> None: ...
    def get(self, tool_name: str, version: str | None = None) -> ToolManifest: ...
    def resolve(self, capability: str, constraints: dict) -> ToolManifest: ...
    def validate_input(self, tool_ref: ToolRef, input_data: dict) -> None: ...
    def validate_output(self, tool_ref: ToolRef, output_data: dict) -> None: ...
    def list_tools(self, filters: ToolFilter) -> list[ToolManifest]: ...
```

Планировщик ищет tool **по capability**, а не по имени:

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

Реестр возвращает подходящий tool/version. Это позволяет менять реализации без переписывания планировщика.

## 6. Артефакты как «универсальный кабель»

Никаких прямых вызовов между модулями. Всё крупное — через `ArtifactStore`.

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

`ArtifactStore` API:

```python
class ArtifactStore:
    def put(self, artifact: ArtifactCreateRequest) -> ArtifactDescriptor: ...
    def get(self, artifact_id: str) -> ArtifactDescriptor: ...
    def open(self, artifact_id: str) -> bytes: ...
    def derive(self, parent_ids: list[str], artifact: ArtifactCreateRequest) -> ArtifactDescriptor: ...
```

Артефакты неизменяемы (immutable). Любая «новая версия» — новый artifact с указанием родителей.

### Базовые типы артефактов первой волны

```
FileSnapshot           — снимок файла в момент чтения
DirectoryListing       — листинг каталога
CommandOutput          — stdout/stderr/exit_code
LogExcerpt             — выдержка из лога с границами
ExcelWorkbook          — ссылка на исходный xlsx
ExcelProfile           — результат profile_excel
ExcelAnomalyReport     — результат detect_excel_anomalies
FilePatch              — diff с base_sha256
PatchPlan              — серия FilePatch с условиями применения
DiagnosticBundle       — собранная диагностика (логи + state)
HypothesisSet          — гипотезы и их ранжирование
ExperimentPlan         — серия безопасных экспериментов
EvidenceBundle         — пакет evidence для финального claim
MarkdownReport         — финальный отчёт
DocxArtifact           — экспорт отчёта в .docx
```

## 7. PlanStep

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

  "depends_on": ["step_001", "step_002"],

  "requires": ["file_read_receipt", "todo_checked"],

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
    ],
    "rollback": {
      "supported": false
    }
  }
}
```

`depends_on` — основа step\_barrier; `requires` — основа Action Gate Matrix; `verification` — основа Verifier; `on_failure` — основа state machine.

## 8. Версионирование

| Уровень | Что версионируется | Когда менять |
|---------|---------------------|---------------|
| `protocol_version` | Envelope’ы (Invocation/Result/Artifact/Event/Evidence/Policy/Memory) | Очень редко, только под major-bump |
| `tool.version` | Реализация конкретного tool | Patch — bug fix; minor — добавление поля; major — ломающие изменения |
| `input/output schema_version` | JSON Schema конкретного tool | Совместимо в minor; major — adapter |
| `artifact.schema_version` | Тип артефакта | Patch/minor — совместимо; major — adapter |

Правила совместимости:

```
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

`ArtifactAdapter` позволяет перевести артефакт между minor/major версиями:

```python
class ArtifactAdapter:
    def can_convert(self, from_type, from_version, to_type, to_version) -> bool: ...
    def convert(self, artifact_id: str, target_version: str) -> str: ...
```

Если адаптера нет — реестр запрещает связку tools.

## 9. Pure tools vs Effect tools

```
Pure tools                          Effect tools
----------                          ------------
parse_log                           apply_patch
profile_excel                       run_command
detect_anomalies                    install_package
generate_patch  (создаёт diff)      restart_container
summarize_document                  modify_firewall
classify_error                      write_memory
inspect_*
read_file
```

Pure tools безопаснее, могут быть AI-generated (после quarantine). Effect tools пишутся вручную/проверяются особенно тщательно. Эффект — только через kernel: модуль не «применяет patch», он **возвращает FilePatch artifact**, а Kernel сам решает, можно ли его применить.

## 10. Pydantic-модели envelope’ов (старт)

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

Эти модели — кандидат на «заморозку» с самого начала. Их менять можно только bump’ом protocol\_version.

## 11. Главное правило проектирования API

**Не расширяй сигнатуры функций. Расширяй схемы данных.** Любое новое требование (resource limits, approval context, dependency evidence) — это новое поле в Pydantic-объекте, а не новый аргумент в `def invoke(...)`.
