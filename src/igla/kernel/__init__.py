"""Kernel composition.

The kernel is the deterministic backbone. Orchestrator/planner code never
mutates state directly; it goes through these services. Each service has a
narrow, frozen contract; replacement (e.g. SQLite-backed event store later)
must preserve those contracts.
"""
from .artifact_store import ArtifactStore
from .clock import Clock, SystemClock
from .errors import KernelError, ToolNotFoundError, ToolValidationError
from .event_store import EventStore
from .evidence_store import EvidenceStore
from .executor import Executor, ExecutorContext
from .kernel import Kernel
from .receipt_manager import ReceiptManager
from .registry import ToolRegistry
from .rollback_manager import RollbackError, RollbackManager
from .schema_validator import SchemaValidator
from .state_machine import StateMachine
from .task_work_log import TaskWorkLog

__all__ = [
    "ArtifactStore",
    "Clock",
    "EventStore",
    "EvidenceStore",
    "Executor",
    "ExecutorContext",
    "Kernel",
    "KernelError",
    "ReceiptManager",
    "RollbackError",
    "RollbackManager",
    "SchemaValidator",
    "StateMachine",
    "SystemClock",
    "TaskWorkLog",
    "ToolNotFoundError",
    "ToolValidationError",
]
