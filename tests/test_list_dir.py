"""Tests for list_dir tool."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from igla.policies.predicates import _DISCOVERY_TOOL_NAMES, PREDICATES, PredicateContext
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.protocol.policy import ActionRequest, PolicyDecisionKind
from igla.tools.builtin.list_dir import _DEPTH_LIMIT, ListDirTool


def _tool(tmp_path: Path) -> ListDirTool:
    return ListDirTool(workspace_root=str(tmp_path))


def _inv(tmp_path: Path, **fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_test",
        task_id="task_test",
        tool=ToolRef(name="list_dir", version="1.0.0"),
        input=fields,
    )


def _action(**kwargs) -> ActionRequest:
    return ActionRequest(actor="planner", task_id="t1", **kwargs)


# ---------------------------------------------------------------------------
# Basic listing
# ---------------------------------------------------------------------------


def test_empty_workspace(tmp_path):
    result = _tool(tmp_path).invoke(_inv(tmp_path))
    assert result.status == "success"
    out = result.output
    assert out["total_files"] == 0
    assert out["total_dirs"] == 0
    assert out["entries"] == []
    assert out["depth_truncated"] is False
    assert out["truncated"] is False


def test_flat_files_depth_0(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=0))
    assert result.status == "success"
    out = result.output
    assert out["total_files"] == 2
    rels = {e["relative_path"] for e in out["entries"]}
    assert "a.txt" in rels
    assert "b.txt" in rels
    assert out["depth"] == 0
    assert out["depth_limit"] == _DEPTH_LIMIT


def test_single_subdir_depth_0_not_expanded(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=0))
    out = result.output
    entries = out["entries"]
    assert len(entries) == 1
    d = entries[0]
    assert d["type"] == "dir"
    assert d["expanded"] is False
    assert d["file_count"] == 1
    assert d["dir_count"] == 0
    assert out["depth_truncated"] is True
    # Files inside unexpanded dirs not counted in total_files
    assert out["total_files"] == 0


def test_single_subdir_depth_1_expanded(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("hello")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=1))
    out = result.output
    entries = out["entries"]
    types = [e["type"] for e in entries]
    assert "dir" in types
    assert "file" in types
    dir_entry = next(e for e in entries if e["type"] == "dir")
    assert dir_entry["expanded"] is True
    assert dir_entry["file_count"] == 1
    assert dir_entry["dir_count"] == 0
    assert out["depth_truncated"] is False
    assert out["total_files"] == 1


def test_nested_dirs_default_depth(tmp_path):
    # Create a/b/c structure
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "c").mkdir()
    (tmp_path / "a" / "b" / "c" / "deep.txt").write_text("deep")
    result = _tool(tmp_path).invoke(_inv(tmp_path))  # default depth=1
    out = result.output
    # a is at level 0 and should be expanded (level=0 < depth=1)
    a_entry = next(e for e in out["entries"] if e["relative_path"] == "a")
    assert a_entry["expanded"] is True
    # a/b is at level 1 and should NOT be expanded (level=1 < depth=1 is False)
    b_entry = next(e for e in out["entries"] if e["relative_path"] == "a/b")
    assert b_entry["expanded"] is False
    assert out["depth_truncated"] is True


def test_dfs_order(tmp_path):
    # a/ with a/x.txt, then b/ with b/y.txt
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.txt").write_text("x")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "y.txt").write_text("y")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=1))
    rels = [e["relative_path"] for e in result.output["entries"]]
    # dirs come before files within each level; a before b alphabetically
    assert rels.index("a") < rels.index("a/x.txt")
    assert rels.index("b") < rels.index("b/y.txt")
    assert rels.index("a") < rels.index("b")


def test_depth_level_field(tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "d2").mkdir()
    (tmp_path / "d1" / "d2" / "f.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=2))
    out = result.output
    d1 = next(e for e in out["entries"] if e["relative_path"] == "d1")
    d2 = next(e for e in out["entries"] if e["relative_path"] == "d1/d2")
    f = next(e for e in out["entries"] if e["relative_path"] == "d1/d2/f.txt")
    assert d1["depth_level"] == 0
    assert d2["depth_level"] == 1
    assert f["depth_level"] == 2


def test_file_size_reported(tmp_path):
    content = "hello world"
    (tmp_path / "hello.txt").write_text(content)
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=0))
    f = next(e for e in result.output["entries"] if e["type"] == "file")
    assert f["size_bytes"] == len(content.encode())


def test_total_size_bytes_accumulates(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world!")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=0))
    assert result.output["total_size_bytes"] == len(b"hello") + len(b"world!")


# ---------------------------------------------------------------------------
# Path resolution and workspace bounds
# ---------------------------------------------------------------------------


def test_default_path_is_workspace_root(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path))
    assert result.output["root"] == "."


def test_subdir_path(tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "m.py").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="pkg", depth=0))
    assert result.status == "success"
    assert result.output["root"] == "pkg"
    assert result.output["total_files"] == 1


def test_path_outside_workspace_rejected(tmp_path):
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="/etc/passwd"))
    assert result.status == "failed"
    assert result.error.code == "PATH_OUTSIDE_WORKSPACE"


def test_path_not_found(tmp_path):
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="nonexistent"))
    assert result.status == "failed"
    assert result.error.code == "DIR_NOT_FOUND"


def test_path_is_file_rejected(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, path="f.txt"))
    assert result.status == "failed"
    assert result.error.code == "NOT_A_DIRECTORY"


# ---------------------------------------------------------------------------
# Hidden files
# ---------------------------------------------------------------------------


def test_hidden_excluded_by_default(tmp_path):
    (tmp_path / ".hidden").write_text("secret")
    (tmp_path / "visible.txt").write_text("ok")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=0))
    rels = {e["relative_path"] for e in result.output["entries"]}
    assert ".hidden" not in rels
    assert "visible.txt" in rels


def test_hidden_included_when_requested(tmp_path):
    (tmp_path / ".hidden").write_text("secret")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=0, include_hidden=True))
    rels = {e["relative_path"] for e in result.output["entries"]}
    assert ".hidden" in rels


def test_hidden_dir_excluded_by_default(tmp_path):
    hidden_dir = tmp_path / ".config"
    hidden_dir.mkdir()
    (hidden_dir / "cfg.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=1))
    rels = {e["relative_path"] for e in result.output["entries"]}
    assert ".config" not in rels
    assert ".config/cfg.txt" not in rels


# ---------------------------------------------------------------------------
# Skip dirs
# ---------------------------------------------------------------------------


def test_skipped_dirs_not_listed(tmp_path):
    for name in ("__pycache__", ".git", "node_modules", ".venv"):
        d = tmp_path / name
        d.mkdir()
        (d / "x.py").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=1))
    rels = {e["relative_path"] for e in result.output["entries"]}
    for name in ("__pycache__", ".git", "node_modules", ".venv"):
        assert name not in rels


# ---------------------------------------------------------------------------
# Depth behaviour
# ---------------------------------------------------------------------------


def test_depth_4_max_no_truncation(tmp_path):
    """At _DEPTH_LIMIT the tool runs fine; depth_truncated only if tree is deeper."""
    d = tmp_path
    for _ in range(_DEPTH_LIMIT):
        d = d / "sub"
        d.mkdir()
    (d / "leaf.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=_DEPTH_LIMIT))
    assert result.status == "success"
    assert result.output["depth_truncated"] is False


def test_depth_truncated_when_tree_deeper_than_requested(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x")
    result = _tool(tmp_path).invoke(_inv(tmp_path, depth=1))
    assert result.output["depth_truncated"] is True


# ---------------------------------------------------------------------------
# Constitution predicate: list_dir_depth_limit
# ---------------------------------------------------------------------------


def test_constitution_predicate_registered():
    assert "list_dir_depth_limit" in PREDICATES


def test_depth_limit_predicate_allows_within_limit():
    fn = PREDICATES["list_dir_depth_limit"]
    action = _action(kind="tool_invocation", tool_name="list_dir", input={"depth": _DEPTH_LIMIT})
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=MagicMock())
    assert fn(ctx).decision.value == "allow"


def test_depth_limit_predicate_denies_above_limit():
    fn = PREDICATES["list_dir_depth_limit"]
    action = _action(
        kind="tool_invocation", tool_name="list_dir", input={"depth": _DEPTH_LIMIT + 1}
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=MagicMock())
    outcome = fn(ctx)
    assert outcome.decision is PolicyDecisionKind.DENY
    assert outcome.rejection.reason_code == "DEPTH_LIMIT_EXCEEDED"
    assert str(_DEPTH_LIMIT + 1) in outcome.rejection.message
    assert str(_DEPTH_LIMIT) in outcome.rejection.message


def test_depth_limit_predicate_passes_non_list_dir():
    fn = PREDICATES["list_dir_depth_limit"]
    action = _action(kind="tool_invocation", tool_name="read_file", input={"path": "foo.txt"})
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=MagicMock())
    assert fn(ctx).decision.value == "allow"


def test_depth_limit_predicate_allows_no_depth_field():
    fn = PREDICATES["list_dir_depth_limit"]
    action = _action(kind="tool_invocation", tool_name="list_dir", input={"path": "."})
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=MagicMock())
    assert fn(ctx).decision.value == "allow"


# ---------------------------------------------------------------------------
# Discovery integration
# ---------------------------------------------------------------------------


def test_list_dir_is_discovery_tool():
    assert "list_dir" in _DISCOVERY_TOOL_NAMES


def test_list_dir_has_discovery_capability():
    with tempfile.TemporaryDirectory() as d:
        tool = ListDirTool(workspace_root=d)
        caps = set(tool.manifest.capabilities)
    assert "fs.discover" in caps or "fs.list" in caps
