"""Kernel-level exceptions.

Tools must NOT raise; they return ``ToolResult(status="failed", error=...)``.
The exceptions in this module are reserved for kernel/runtime faults that
happen *outside* a tool invocation contract.
"""
from __future__ import annotations


class KernelError(Exception):
    """Base class for kernel faults."""


class ToolNotFoundError(KernelError):
    pass


class ToolValidationError(KernelError):
    """Raised by SchemaValidator when an envelope or input fails its schema."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class PolicyConfigurationError(KernelError):
    pass


class StateTransitionError(KernelError):
    pass


class ArtifactStoreError(KernelError):
    pass


class ReceiptError(KernelError):
    pass
