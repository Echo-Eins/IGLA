"""Tools — single-purpose modules invoked through the kernel executor.

Tools are *registered* in ``ToolRegistry`` and *executed* in ``Executor``.
A tool implements ``Tool.invoke(ToolInvocation) -> ToolResult`` and never
imports from ``kernel`` or ``orchestrator``: it sees only its envelope.
"""
from .base import Tool

__all__ = ["Tool"]
