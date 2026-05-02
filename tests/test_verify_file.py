"""Tests for receipt-gated verify_file."""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import igla.tools.builtin.verify_file as verify_mod
from igla.kernel.clock import StepClock
from igla.kernel.receipt_manager import ReceiptManager
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.tools.builtin.read_file import ReadFileTool
from igla.tools.builtin.verify_file import VerifyFileTool


def _build(tmp_path: Path) -> tuple[ReadFileTool, VerifyFileTool, ReceiptManager]:
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=UTC))
    receipts = ReceiptManager(tmp_path / ".receipts", clock)
    read_tool = ReadFileTool(workspace_root=str(tmp_path), receipts=receipts)
    verify_tool = VerifyFileTool(workspace_root=str(tmp_path), receipts=receipts)
    return read_tool, verify_tool, receipts


def _read_inv(path: str) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_read",
        task_id="task_test",
        tool=ToolRef(name="read_file", version="1.0.0"),
        input={"path": path},
    )


def _verify_inv(path: str, **fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_verify",
        task_id="task_test",
        step_id="step_verify",
        tool=ToolRef(name="verify_file", version="1.0.0"),
        input={"path": path, **fields},
    )


def _read_first(tmp_path: Path, path: str) -> VerifyFileTool:
    read_tool, verify_tool, _ = _build(tmp_path)
    result = read_tool.invoke(_read_inv(path))
    assert result.status == "success", result
    return verify_tool


def test_verify_file_requires_prior_receipt(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
    _, verify_tool, _ = _build(tmp_path)

    result = verify_tool.invoke(_verify_inv("x.py", checks=["syntax"]))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "NO_RECEIPT_FOR_PATH"


def test_python_syntax_failure_is_successful_invocation_with_failed_check(
    tmp_path: Path,
) -> None:
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    verify_tool = _read_first(tmp_path, "bad.py")

    result = verify_tool.invoke(_verify_inv("bad.py", checks=["syntax"]))

    assert result.status == "success"
    assert result.output["overall_passed"] is False
    assert result.output["checks_run"] == ["syntax"]
    check = result.output["results"][0]
    assert check["check"] == "syntax"
    assert check["passed"] is False
    assert "SyntaxError" in check["error"]


def test_json_and_yaml_syntax_checks(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text('{"answer": 42}\n', encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("root: [unterminated\n", encoding="utf-8")

    verify_json = _read_first(tmp_path, "ok.json")
    json_result = verify_json.invoke(_verify_inv("ok.json"))
    assert json_result.status == "success"
    assert json_result.output["file_type"] == "json"
    assert json_result.output["checks_run"] == ["syntax"]
    assert json_result.output["overall_passed"] is True

    verify_yaml = _read_first(tmp_path, "bad.yaml")
    yaml_result = verify_yaml.invoke(_verify_inv("bad.yaml"))
    assert yaml_result.status == "success"
    assert yaml_result.output["file_type"] == "yaml"
    assert yaml_result.output["overall_passed"] is False
    assert "YAMLError" in yaml_result.output["results"][0]["error"]


def test_python_auto_checks_use_current_interpreter_for_ruff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "ok.py").write_text("VALUE = 1\n", encoding="utf-8")
    verify_tool = _read_first(tmp_path, "ok.py")
    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(verify_mod.subprocess, "run", _fake_run)

    result = verify_tool.invoke(_verify_inv("ok.py"))

    assert result.status == "success"
    assert result.output["checks_run"] == ["syntax", "lint"]
    assert result.output["overall_passed"] is True
    assert captured["cmd"][:3] == [sys.executable, "-m", "ruff"]
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())


def test_lint_failure_reports_output_without_tool_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "lint_bad.py").write_text("import os\n", encoding="utf-8")
    verify_tool = _read_first(tmp_path, "lint_bad.py")

    def _fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="F401 unused import\n", stderr="")

    monkeypatch.setattr(verify_mod.subprocess, "run", _fake_run)

    result = verify_tool.invoke(_verify_inv("lint_bad.py", checks=["lint"]))

    assert result.status == "success"
    assert result.output["overall_passed"] is False
    check = result.output["results"][0]
    assert check["check"] == "lint"
    assert check["exit_code"] == 1
    assert "F401" in check["error"]


def test_pytest_requires_test_path(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    verify_tool = _read_first(tmp_path, "app.py")

    result = verify_tool.invoke(_verify_inv("app.py", checks=["pytest"]))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "PYTEST_TEST_PATH_REQUIRED"


def test_pytest_path_must_stay_inside_workspace(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    verify_tool = _read_first(tmp_path, "app.py")

    result = verify_tool.invoke(
        _verify_inv("app.py", checks=["pytest"], test_path="/etc/passwd")
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "PYTEST_PATH_OUTSIDE_WORKSPACE"


def test_pytest_uses_current_interpreter_and_workspace_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    verify_tool = _read_first(tmp_path, "app.py")
    captured: dict[str, object] = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="1 passed\n", stderr="")

    monkeypatch.setattr(verify_mod.subprocess, "run", _fake_run)

    result = verify_tool.invoke(
        _verify_inv("app.py", checks=["pytest"], test_path="tests/test_app.py")
    )

    assert result.status == "success"
    assert result.output["overall_passed"] is True
    assert captured["cmd"][:3] == [sys.executable, "-m", "pytest"]
    assert str(tests_dir / "test_app.py") in captured["cmd"]
    assert captured["kwargs"]["cwd"] == str(tmp_path.resolve())
