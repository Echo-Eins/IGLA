"""ToolManifest — the passport of every registered tool.

Manifests are loaded from in-process registration (Python tools) and may in
the future come from MCP-style adapters or quarantine promotions.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ToolNamespace = str  # e.g. "core.todo", "fs.read", "user.io"
ToolRiskLevel = Literal[
    "read_only", "create_only", "mutating", "execution", "system", "privileged"
]


class ResourceLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeout_seconds: int = 120
    max_memory_mb: int = 4096
    max_file_size_mb: int = 512


class SandboxSpec(BaseModel):
    """Sandbox requirements as declared by the tool.

    Until the sandbox subsystem (06-sandbox-security.md) lands, the executor
    runs tools in-process and merely records the declared profile in events.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: str = "in_process"  # placeholder until sandbox lands
    network: Literal["disabled", "declared_only", "full"] = "disabled"


class ArtifactSpec(BaseModel):
    """Pattern describing the artifacts a tool consumes/produces.

    The ``schema`` constraint uses a SemVer-like pattern: ``1.x`` matches any
    1.minor.patch; ``1.2.x`` matches any 1.2.patch. Renamed from ``schema``
    to avoid shadowing ``BaseModel.schema()``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str
    schema_pattern: str  # e.g. "1.x"


class PolicyRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requires: list[str] = Field(default_factory=list)
    forbidden_modes: list[str] = Field(default_factory=list)


class VerifierSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = "schema_validation"
    artifact_type: str | None = None


class ToolManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # e.g. "tool.core.ask_user"
    name: str  # short name used in invocations: "ask_user"
    namespace: ToolNamespace
    version: str

    description: str
    summary: str | None = None

    capabilities: list[str] = Field(default_factory=list)

    risk_level: ToolRiskLevel = "read_only"
    side_effects: bool = False

    input_schema: dict[str, Any] = Field(default_factory=dict)  # JSON Schema (draft 2020-12)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    consumes: list[ArtifactSpec] = Field(default_factory=list)
    produces: list[ArtifactSpec] = Field(default_factory=list)

    policies: PolicyRequirements = Field(default_factory=PolicyRequirements)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)
    verifier: VerifierSpec | None = None

    protocol_compatibility: str = ">=1.0,<2.0"
    extensions: dict[str, Any] = Field(default_factory=dict)
