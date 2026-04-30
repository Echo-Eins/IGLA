"""Workspace discovery tools."""
from __future__ import annotations

from pathlib import Path

from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.tools.builtin.search import FindFilesTool, SearchTextTool


def _invocation(tool_name: str, input_data: dict) -> ToolInvocation:
    return ToolInvocation(
        invocation_id=f"inv_{tool_name}",
        task_id="task_discovery",
        tool=ToolRef(name=tool_name, version="1.0.0"),
        input=input_data,
    )


def test_find_files_locates_file_by_basename(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    docs = workspace / "docs"
    docs.mkdir(parents=True)
    target = docs / "00-review.md"
    target.write_text("# Review\n", encoding="utf-8")

    tool = FindFilesTool(workspace_root=str(workspace))
    result = tool.invoke(
        _invocation(
            "find_files",
            {"query": "00-review.md", "max_results": 10},
        )
    )

    assert result.status == "success"
    assert result.output["count"] == 1
    assert result.output["matches"][0]["relative_path"] == "docs/00-review.md"


def test_find_files_supports_glob(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "00-review.md").write_text("# Review\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("x\n", encoding="utf-8")

    tool = FindFilesTool(workspace_root=str(workspace))
    result = tool.invoke(_invocation("find_files", {"glob": "docs/*.md"}))

    assert result.status == "success"
    assert [m["relative_path"] for m in result.output["matches"]] == ["docs/00-review.md"]


def test_search_text_finds_line_matches(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    docs = workspace / "docs"
    docs.mkdir(parents=True)
    (docs / "00-review.md").write_text("alpha\nneedle here\nomega\n", encoding="utf-8")

    tool = SearchTextTool(workspace_root=str(workspace))
    result = tool.invoke(
        _invocation(
            "search_text",
            {"query": "needle", "path_glob": "docs/*.md"},
        )
    )

    assert result.status == "success"
    assert result.output["count"] == 1
    match = result.output["matches"][0]
    assert match["relative_path"] == "docs/00-review.md"
    assert match["line_number"] == 2
    assert match["line"] == "needle here"


def test_search_text_skips_hidden_dirs_by_default(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    hidden = workspace / ".igla"
    hidden.mkdir(parents=True)
    (hidden / "state.txt").write_text("needle\n", encoding="utf-8")

    tool = SearchTextTool(workspace_root=str(workspace))
    result = tool.invoke(_invocation("search_text", {"query": "needle"}))

    assert result.status == "success"
    assert result.output["count"] == 0
