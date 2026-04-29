"""Kernel facade.

``Kernel`` wires the deterministic services together. Higher layers
(orchestrator, planner, chat REPL) talk to ``Kernel`` only — never directly
to a service — so we can swap implementations later without leakage.
"""
from __future__ import annotations

from pathlib import Path

from ..config import IglaSettings
from .artifact_store import ArtifactStore
from .clock import Clock, SystemClock
from .event_store import EventStore
from .evidence_store import EvidenceStore
from .executor import Executor, ExecutorContext
from .receipt_manager import ReceiptManager
from .registry import ToolRegistry
from .schema_validator import SchemaValidator
from .state_machine import StateMachine


class Kernel:
    """Wired collection of kernel services. Construct once per process."""

    def __init__(
        self,
        settings: IglaSettings,
        *,
        clock: Clock | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()

        paths = settings.paths
        paths.state_dir.mkdir(parents=True, exist_ok=True)

        self.events = EventStore(paths.events_file, self.clock)
        self.artifacts = ArtifactStore(paths.artifacts_dir, self.clock)
        self.evidence = EvidenceStore(paths.state_dir / "evidence.jsonl", self.clock)
        self.receipts = ReceiptManager(paths.receipts_dir, self.clock)
        self.state = StateMachine(self.clock)
        self.validator = SchemaValidator()
        self.registry = registry or ToolRegistry()
        self.executor = Executor(
            self.registry,
            self.validator,
            ExecutorContext(workspace_root=str(paths.workspace)),
        )

    @property
    def workspace(self) -> Path:
        return self.settings.paths.workspace
