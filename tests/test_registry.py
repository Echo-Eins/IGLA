"""ToolRegistry tests."""
from __future__ import annotations

import pytest

from igla.kernel.errors import KernelError, ToolNotFoundError
from igla.kernel.registry import ToolRegistry
from igla.tools.builtin.noop_observe import NoopObserveTool


def test_register_and_lookup() -> None:
    reg = ToolRegistry()
    tool = NoopObserveTool()
    reg.register(tool)
    manifest = reg.get("noop_observe", "1.0.0")
    assert manifest.name == "noop_observe"
    assert reg.get_tool("noop_observe") is tool


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register(NoopObserveTool())
    with pytest.raises(KernelError):
        reg.register(NoopObserveTool())


def test_capability_resolution() -> None:
    reg = ToolRegistry()
    reg.register(NoopObserveTool())
    manifest = reg.resolve("observe.note", {})
    assert manifest.name == "noop_observe"


def test_missing_tool_raises() -> None:
    reg = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        reg.get("nope")
    with pytest.raises(ToolNotFoundError):
        reg.resolve("missing.cap", {})
