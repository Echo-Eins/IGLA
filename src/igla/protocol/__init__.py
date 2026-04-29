"""Frozen protocol envelopes for IGLA.

Stability guarantees:
* Adding optional fields → patch / minor bump.
* Renaming, removing, retyping fields → major bump only.
* No envelope object is mutated after construction; updates are made by
  constructing a new value via ``.model_copy(update=...)``.

See docs/04-tool-protocol.md.
"""
from .artifact import ArtifactCreateRequest, ArtifactDescriptor, ArtifactRef
from .evidence import EvidenceCreateRequest, EvidenceKind, EvidenceRecord, EvidenceRef
from .event import EventKind, EventRecord
from .invocation import ToolInvocation, ToolRef
from .manifest import (
    ArtifactSpec,
    PolicyRequirements,
    ResourceLimits,
    SandboxSpec,
    ToolManifest,
    ToolNamespace,
    ToolRiskLevel,
    VerifierSpec,
)
from .plan import Plan, PlanStep, StepKind, StepStatus
from .policy import (
    ActionRequest,
    PolicyDecision,
    PolicyDecisionKind,
    PolicyRejection,
)
from .proposal import (
    AskUserClarificationProposal,
    DeclareTaskDoneProposal,
    PlannerProposal,
    ProposalKind,
    TodoBranchProposal,
    TodoCompleteProposal,
    ToolInvocationProposal,
)
from .receipt import FileReadReceipt, ReceiptKind
from .runtime import RuntimeMode, RuntimeStateSnapshot
from .task import TaskSpec, TaskStatus
from .todo import TodoNode, TodoNodeKind, TodoStatus, TodoTreeSnapshot
from .result import ToolError, ToolResult, ToolResultStatus

__all__ = [
    "ArtifactCreateRequest",
    "ArtifactDescriptor",
    "ArtifactRef",
    "ArtifactSpec",
    "ActionRequest",
    "AskUserClarificationProposal",
    "DeclareTaskDoneProposal",
    "EvidenceCreateRequest",
    "EvidenceKind",
    "EvidenceRecord",
    "EvidenceRef",
    "EventKind",
    "EventRecord",
    "FileReadReceipt",
    "Plan",
    "PlanStep",
    "PlannerProposal",
    "PolicyDecision",
    "PolicyDecisionKind",
    "PolicyRejection",
    "PolicyRequirements",
    "ProposalKind",
    "ReceiptKind",
    "ResourceLimits",
    "RuntimeMode",
    "RuntimeStateSnapshot",
    "SandboxSpec",
    "StepKind",
    "StepStatus",
    "TaskSpec",
    "TaskStatus",
    "TodoBranchProposal",
    "TodoCompleteProposal",
    "TodoNode",
    "TodoNodeKind",
    "TodoStatus",
    "TodoTreeSnapshot",
    "ToolError",
    "ToolInvocation",
    "ToolInvocationProposal",
    "ToolManifest",
    "ToolNamespace",
    "ToolRef",
    "ToolResult",
    "ToolResultStatus",
    "ToolRiskLevel",
    "VerifierSpec",
]
