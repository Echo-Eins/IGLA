"""Workspace state reset behavior."""
from __future__ import annotations

import argparse

import pytest

from igla.cli import cmd_reset_state
from igla.state_reset import StateResetError, reset_workspace_state


def test_reset_workspace_state_removes_only_workspace_igla(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    state_dir = workspace / ".igla"
    nested = state_dir / "todo" / "task.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}", encoding="utf-8")

    result = reset_workspace_state(workspace=workspace, state_dir=state_dir)

    assert result.removed is True
    assert not state_dir.exists()
    assert workspace.exists()


def test_reset_workspace_state_reports_absent_dir(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = reset_workspace_state(workspace=workspace, state_dir=workspace / ".igla")

    assert result.removed is False
    assert result.state_dir == (workspace / ".igla").resolve()


def test_reset_workspace_state_refuses_non_workspace_path(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside" / ".igla"
    workspace.mkdir()
    outside.mkdir(parents=True)

    with pytest.raises(StateResetError):
        reset_workspace_state(workspace=workspace, state_dir=outside)


def test_reset_state_subcommand_uses_configured_workspace(make_settings, capsys) -> None:
    settings = make_settings()
    poison = settings.paths.state_dir / "events.jsonl"
    poison.parent.mkdir(parents=True)
    poison.write_text("bad", encoding="utf-8")
    args = argparse.Namespace(
        workspace=str(settings.paths.workspace),
        lm_url=None,
        lm_model=None,
        lm_key=None,
        no_schema=False,
    )

    status = cmd_reset_state(args)

    assert status == 0
    assert not poison.exists()
    out = capsys.readouterr().out
    assert "removed state_dir:" in out
