"""ToolRegistry tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from igla.kernel.artifact_store import ArtifactStore
from igla.kernel.clock import StepClock
from igla.kernel.errors import KernelError, ToolNotFoundError
from igla.kernel.event_store import EventStore
from igla.kernel.receipt_manager import ReceiptManager
from igla.kernel.registry import ToolRegistry
from igla.kernel.rollback_manager import RollbackManager
from igla.kernel.task_work_log import TaskWorkLog
from igla.tools.builtin import build_default_toolset
from igla.tools.builtin.noop_observe import NoopObserveTool


class _DummyAsk:
    def ask(self, *, question: str, prompt_label: str | None = None) -> str:
        return ""


def test_register_and_lookup() -> None:
    reg = ToolRegistry()
    tool = NoopObserveTool()
    reg.register(tool)
    manifest = reg.get("noop_observe", "1.0.0")
    assert manifest.name == "noop_observe"
    assert reg.get_tool("noop_observe") is tool


def test_lookup_accepts_v_prefixed_version_refs() -> None:
    reg = ToolRegistry()
    tool = NoopObserveTool()
    reg.register(tool)

    manifest = reg.get("noop_observe", "v1.0.0")

    assert manifest.version == "1.0.0"
    assert reg.has("noop_observe", "V1.0.0")
    assert reg.get_tool("noop_observe", "v1.0.0") is tool


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


def test_wrong_version_falls_back_to_registered() -> None:
    """Model-hallucinated versions (e.g. '1.We...') resolve to actual version."""
    reg = ToolRegistry()
    tool = NoopObserveTool()
    reg.register(tool)

    # Exact wrong version should still find the tool (name-based fallback).
    assert reg.has("noop_observe", "1.We...")
    assert reg.has("noop_observe", "9.9.9")
    manifest = reg.get("noop_observe", "garbage-version")
    assert manifest.version == "1.0.0"
    assert reg.get_tool("noop_observe", "wrong") is tool

    # Truly unknown tool name still fails.
    assert not reg.has("no_such_tool", "1.0.0")
    with pytest.raises(ToolNotFoundError):
        reg.get("no_such_tool", "1.0.0")


def test_build_default_toolset_includes_verifier_and_task_log(tmp_path) -> None:
    clock = StepClock(datetime(2026, 5, 1, tzinfo=UTC))
    artifacts = ArtifactStore(tmp_path / "artifacts", clock)
    receipts = ReceiptManager(tmp_path / "receipts", clock)
    rollback = RollbackManager(artifacts, clock)
    work_log = TaskWorkLog(EventStore(tmp_path / "events.jsonl", clock))

    tools = build_default_toolset(
        ask_user_channel=_DummyAsk(),
        workspace_root=str(tmp_path),
        receipts=receipts,
        rollback=rollback,
        work_log=work_log,
    )

    names = {tool.manifest.name for tool in tools}
    assert "verify_file" in names
    assert "read_task_log" in names
    assert "patch_file" in names
    assert "restore_file" in names
