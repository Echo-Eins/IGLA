"""Tests for copy_file."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from igla.kernel.artifact_store import ArtifactStore
from igla.kernel.clock import StepClock
from igla.kernel.receipt_manager import ReceiptManager
from igla.kernel.rollback_manager import RollbackManager
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.tools.builtin.copy_file import CopyFileTool


def _build(tmp_path: Path) -> tuple[CopyFileTool, ReceiptManager]:
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=UTC))
    receipts = ReceiptManager(tmp_path / ".receipts", clock)
    artifacts = ArtifactStore(tmp_path / ".artifacts", clock)
    rollback = RollbackManager(artifacts, clock)
    return (
        CopyFileTool(
            workspace_root=str(tmp_path),
            receipts=receipts,
            rollback=rollback,
        ),
        receipts,
    )


def _inv(**fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_copy",
        task_id="task_copy",
        step_id="step_copy",
        tool=ToolRef(name="copy_file", version="1.0.0"),
        input=fields,
    )


def test_copy_file_creates_destination_with_append_text(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_bytes(b"body\n")
    tool, receipts = _build(tmp_path)

    result = tool.invoke(
        _inv(
            source_path="README.md",
            destination_path="README1.md",
            append_text="hello\n",
        )
    )

    assert result.status == "success"
    assert (tmp_path / "README1.md").read_text(encoding="utf-8") == "body\nhello\n"
    out = result.output
    assert out["bytes_source"] == len(b"body\n")
    assert out["bytes_written"] == len(b"body\nhello\n")
    assert out["appended_bytes"] == len(b"hello\n")
    assert out["overwrote"] is False
    assert out["backup_artifact_id"] is None
    receipt = receipts.get_file_read("task_copy", str(tmp_path / "README1.md"))
    assert receipt is not None
    assert receipt.file_sha256 == out["sha256_after"]


def test_copy_file_refuses_existing_destination_by_default(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "dest.txt").write_text("old\n", encoding="utf-8")
    tool, _ = _build(tmp_path)

    result = tool.invoke(_inv(source_path="source.txt", destination_path="dest.txt"))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "DESTINATION_EXISTS"
    assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "old\n"


def test_copy_file_overwrite_creates_backup(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "dest.txt").write_text("old\n", encoding="utf-8")
    tool, _ = _build(tmp_path)

    result = tool.invoke(
        _inv(source_path="source.txt", destination_path="dest.txt", overwrite=True)
    )

    assert result.status == "success"
    assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "new\n"
    assert result.output["overwrote"] is True
    assert result.output["backup_artifact_id"]
    assert result.output["rollback_plan_id"]


def test_copy_file_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("new\n", encoding="utf-8")
    tool, _ = _build(tmp_path)

    result = tool.invoke(
        _inv(source_path="source.txt", destination_path=str(tmp_path.parent / "x.txt"))
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "DESTINATION_OUTSIDE_WORKSPACE"
