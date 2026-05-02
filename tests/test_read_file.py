"""Tests for read_file line-range reading."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from igla.kernel.clock import StepClock
from igla.kernel.receipt_manager import ReceiptManager
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.tools.builtin.read_file import _LINE_LIMIT, ReadFileTool


def _receipts(tmp_path: Path) -> ReceiptManager:
    d = tmp_path / ".receipts"
    d.mkdir(parents=True, exist_ok=True)
    return ReceiptManager(
        base_dir=d,
        clock=StepClock(start=datetime(2026, 1, 1, tzinfo=UTC)),
    )


def _tool(tmp_path: Path) -> ReadFileTool:
    return ReadFileTool(workspace_root=str(tmp_path), receipts=_receipts(tmp_path))


def _inv(tmp_path: Path, **fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_test",
        task_id="task_test",
        tool=ToolRef(name="read_file", version="1.0.0"),
        input=fields,
    )


def test_small_file_read_whole(tmp_path):
    (tmp_path / "small.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="small.txt"))
    assert result.status == "success"
    out = result.output
    assert out["total_lines"] == 3
    assert out["start_line"] == 0
    assert out["end_line"] == 3
    assert out["end_of_file"] is True
    assert out["truncated"] is False
    assert "line1" in out["content"]
    assert "line3" in out["content"]


def test_large_file_no_range_returns_error(tmp_path):
    big = "\n".join(f"line {i}" for i in range(_LINE_LIMIT + 1))
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="big.txt"))
    assert result.status == "failed"
    assert result.error.code == "FILE_TOO_LARGE"
    assert str(_LINE_LIMIT + 1) in result.error.message
    assert "start_line" in result.error.message
    assert "end_line" in result.error.message


def test_large_file_with_range_succeeds(tmp_path):
    lines = [f"L{i}" for i in range(2500)]
    (tmp_path / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="big.txt", start_line=0, end_line=1000))
    assert result.status == "success"
    out = result.output
    assert out["total_lines"] == 2500
    assert out["start_line"] == 0
    assert out["end_line"] == 1000
    assert out["end_of_file"] is False
    assert "L0" in out["content"]
    assert "L999" in out["content"]
    assert "L1000" not in out["content"]


def test_large_file_with_start_line_only_reads_next_chunk(tmp_path):
    lines = [f"L{i}" for i in range(2500)]
    (tmp_path / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="big.txt", start_line=1000))
    assert result.status == "success"
    out = result.output
    assert out["total_lines"] == 2500
    assert out["start_line"] == 1000
    assert out["end_line"] == 2000
    assert out["end_of_file"] is False
    assert "L1000" in out["content"]
    assert "L1999" in out["content"]
    assert "L2000" not in out["content"]


def test_large_file_start_line_only_last_chunk_sets_end_of_file(tmp_path):
    lines = [f"L{i}" for i in range(2500)]
    (tmp_path / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="big.txt", start_line=2000))
    assert result.status == "success"
    out = result.output
    assert out["start_line"] == 2000
    assert out["end_line"] == 2500
    assert out["end_of_file"] is True
    assert "L2499" in out["content"]


def test_last_chunk_sets_end_of_file(tmp_path):
    lines = [f"L{i}" for i in range(1500)]
    (tmp_path / "big.txt").write_text("\n".join(lines), encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="big.txt", start_line=1000, end_line=2000))
    out = result.output
    assert out["end_of_file"] is True
    assert out["end_line"] == 1500
    assert "L1000" in out["content"]
    assert "L1499" in out["content"]


def test_end_line_beyond_eof_is_capped(tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc\n", encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="f.txt", start_line=0, end_line=999))
    out = result.output
    assert out["end_line"] == 3
    assert out["end_of_file"] is True


def test_start_line_beyond_eof_returns_empty(tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="f.txt", start_line=100, end_line=200))
    out = result.output
    assert out["content"] == ""
    assert out["end_of_file"] is True


def test_file_not_found(tmp_path):
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="nope.txt"))
    assert result.status == "failed"
    assert result.error.code == "FILE_NOT_FOUND"


def test_path_outside_workspace(tmp_path):
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="/etc/passwd"))
    assert result.status == "failed"
    assert result.error.code == "PATH_OUTSIDE_WORKSPACE"


def test_empty_file(tmp_path):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="empty.txt"))
    assert result.status == "success"
    assert result.output["total_lines"] == 0
    assert result.output["content"] == ""
    assert result.output["end_of_file"] is True


def test_exact_limit_file_reads_whole(tmp_path):
    lines = [f"L{i}" for i in range(_LINE_LIMIT)]
    (tmp_path / "exact.txt").write_text("\n".join(lines), encoding="utf-8")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="exact.txt"))
    assert result.status == "success"
    assert result.output["total_lines"] == _LINE_LIMIT
    assert result.output["end_of_file"] is True
