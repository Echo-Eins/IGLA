"""Base class for tools.

A ``Tool`` is a function-like object that implements one capability. The
class form (rather than a bare callable) gives us a stable place to attach
the ``manifest`` and to allow stateful built-ins (e.g. ``ask_user`` carries
a reference to the chat I/O channel).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..protocol.invocation import ToolInvocation
from ..protocol.manifest import ToolManifest
from ..protocol.result import ToolResult


class Tool(ABC):
    """Subclasses must set ``manifest`` and implement ``invoke``.

    Implementations MUST NOT raise. They return a ``ToolResult`` even on
    failure (status="failed", populated ``error``). Raising leaks into the
    executor's "ToolException" path, which is fine but bypasses the
    structured error contract.
    """

    manifest: ToolManifest

    def __init__(self, manifest: ToolManifest) -> None:
        self.manifest = manifest

    @abstractmethod
    def invoke(self, invocation: ToolInvocation) -> ToolResult: ...
