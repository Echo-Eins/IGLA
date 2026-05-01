"""Tests for the patch_file tool and its policy invariants."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from igla.kernel.artifact_store import ArtifactStore
from igla.kernel.clock import StepClock
from igla.kernel.receipt_manager import ReceiptManager
from igla.kernel.rollback_manager import RollbackManager
from igla.policies.predicates import PREDICATES, PredicateContext
from igla.protocol.invocation import ToolInvocation, ToolRef
from igla.protocol.policy import ActionRequest, PolicyDecisionKind
from igla.tools.builtin.patch_file import PatchFileTool
from igla.tools.builtin.read_file import ReadFileTool
from igla.tools.builtin.restore_file import RestoreFileTool


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _build(tmp_path: Path):
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    receipts_dir = tmp_path / ".receipts"
    artifacts_dir = tmp_path / ".artifacts"
    receipts = ReceiptManager(receipts_dir, clock)
    artifacts = ArtifactStore(artifacts_dir, clock)
    rollback = RollbackManager(artifacts, clock)
    read_tool = ReadFileTool(workspace_root=str(tmp_path), receipts=receipts)
    patch_tool = PatchFileTool(
        workspace_root=str(tmp_path),
        receipts=receipts,
        rollback=rollback,
    )
    restore_tool = RestoreFileTool(
        workspace_root=str(tmp_path),
        receipts=receipts,
        rollback=rollback,
    )
    return read_tool, patch_tool, restore_tool, receipts, rollback, artifacts


def _read_inv(tmp_path: Path, path: str, **fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_read",
        task_id="task_test",
        tool=ToolRef(name="read_file", version="1.0.0"),
        input={"path": path, **fields},
    )


def _patch_inv(
    tmp_path: Path,
    path: str,
    *,
    base_sha256: str,
    invocation_id: str = "inv_patch",
    step_id: str | None = "step_patch",
    **fields,
) -> ToolInvocation:
    return ToolInvocation(
        invocation_id=invocation_id,
        task_id="task_test",
        step_id=step_id,
        tool=ToolRef(name="patch_file", version="1.0.0"),
        input={"path": path, "base_sha256": base_sha256, **fields},
    )


def _restore_inv(backup_id: str, **fields) -> ToolInvocation:
    return ToolInvocation(
        invocation_id="inv_restore",
        task_id="task_test",
        step_id="step_restore",
        tool=ToolRef(name="restore_file", version="1.0.0"),
        input={"backup_artifact_id": backup_id, **fields},
    )


# ---------------------------------------------------------------------------#
# read_file now exposes file_sha256
# ---------------------------------------------------------------------------#


def test_read_file_emits_file_sha256(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hello\nworld\n")
    read_tool, *_ = _build(tmp_path)

    result = read_tool.invoke(_read_inv(tmp_path, "f.txt"))
    assert result.status == "success"
    out = result.output
    assert "file_sha256" in out
    assert out["file_sha256"].startswith("sha256:")
    # For a full read, sha256 == file_sha256
    assert out["sha256"] == out["file_sha256"]


def test_read_file_partial_keeps_full_file_sha256(tmp_path):
    lines = "\n".join(f"L{i}" for i in range(2000))
    target = tmp_path / "big.txt"
    target.write_text(lines)
    read_tool, *_ = _build(tmp_path)

    result = read_tool.invoke(
        _read_inv(tmp_path, "big.txt", start_line=0, end_line=500)
    )
    out = result.output
    file_sha = _hash(target.read_bytes())
    assert out["file_sha256"] == file_sha
    # Partial slice has a different hash from the whole file
    assert out["sha256"] != out["file_sha256"]


# ---------------------------------------------------------------------------#
# Happy-path patches
# ---------------------------------------------------------------------------#


def test_full_replace_succeeds(tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("old\n")
    read_tool, patch_tool, *_ = _build(tmp_path)

    rresult = read_tool.invoke(_read_inv(tmp_path, "x.txt"))
    base = rresult.output["file_sha256"]

    presult = patch_tool.invoke(
        _patch_inv(tmp_path, "x.txt", base_sha256=base, new_content="new\n")
    )
    assert presult.status == "success", presult
    out = presult.output
    assert out["patch_mode"] == "full_replace"
    assert out["sha256_before"] == base
    assert out["sha256_after"] != base
    assert out["bytes_after"] == len(b"new\n")
    assert target.read_text() == "new\n"
    assert "backup_artifact_id" in out
    assert out["rollback_plan_id"].startswith("rbp_")


def test_search_replace_single_occurrence(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("DEBUG = False\n")
    read_tool, patch_tool, *_ = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "code.py")).output["file_sha256"]
    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "code.py",
            base_sha256=base,
            search="DEBUG = False",
            replacement="DEBUG = True",
        )
    )
    assert result.status == "success"
    assert result.output["patch_mode"] == "search_replace"
    assert result.output["occurrences_replaced"] == 1
    assert target.read_text() == "DEBUG = True\n"


def test_search_replace_all(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("foo bar foo bar foo\n")
    read_tool, patch_tool, *_ = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=base,
            search="foo",
            replacement="FOO",
            replace_all=True,
        )
    )
    assert result.status == "success"
    assert result.output["occurrences_replaced"] == 3
    assert target.read_text() == "FOO bar FOO bar FOO\n"


def test_patch_creates_backup_artifact_with_original_bytes(tmp_path):
    target = tmp_path / "f.txt"
    original = "ORIGINAL CONTENT\n"
    target.write_text(original)
    read_tool, patch_tool, _, _, rollback, artifacts = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    result = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, new_content="MUTATED\n")
    )
    backup_id = result.output["backup_artifact_id"]

    # Backup artifact must contain the original bytes
    backup_bytes = artifacts.open_bytes(backup_id)
    assert backup_bytes == original.encode("utf-8")

    # The backup descriptor knows the original path
    snap = rollback.get_snapshot(backup_id)
    assert snap.original_path == str(target)


def test_patch_updates_receipt_with_new_hash(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("v1\n")
    read_tool, patch_tool, _, receipts, *_ = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    presult = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, new_content="v2\n")
    )
    new_hash = presult.output["sha256_after"]

    receipt = receipts.get_file_read("task_test", str(target.resolve()))
    assert receipt is not None
    assert receipt.file_sha256 == new_hash


def test_patch_no_op_full_replace_succeeds(tmp_path):
    """new_content equal to current content should still succeed (idempotent)."""
    target = tmp_path / "f.txt"
    target.write_text("same\n")
    read_tool, patch_tool, *_ = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    result = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, new_content="same\n")
    )
    assert result.status == "success"
    assert result.output["sha256_before"] == result.output["sha256_after"]


# ---------------------------------------------------------------------------#
# Failure modes
# ---------------------------------------------------------------------------#


def test_path_outside_workspace(tmp_path):
    _, patch_tool, *_ = _build(tmp_path)
    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "/etc/passwd",
            base_sha256="sha256:00",
            new_content="x",
        )
    )
    assert result.status == "failed"
    assert result.error.code == "PATH_OUTSIDE_WORKSPACE"


def test_file_not_found(tmp_path):
    _, patch_tool, *_ = _build(tmp_path)
    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "missing.txt",
            base_sha256="sha256:00",
            new_content="x",
        )
    )
    assert result.status == "failed"
    assert result.error.code == "FILE_NOT_FOUND"


def test_not_a_file(tmp_path):
    _, patch_tool, *_ = _build(tmp_path)
    sub = tmp_path / "subdir"
    sub.mkdir()
    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "subdir",
            base_sha256="sha256:00",
            new_content="x",
        )
    )
    assert result.status == "failed"
    assert result.error.code == "NOT_A_FILE"


def test_conflicting_patch_modes(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hi")
    read_tool, patch_tool, *_ = _build(tmp_path)
    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]

    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=base,
            new_content="x",
            search="hi",
            replacement="bye",
        )
    )
    assert result.status == "failed"
    assert result.error.code == "CONFLICTING_PATCH_MODES"


def test_missing_patch_content(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hi")
    read_tool, patch_tool, *_ = _build(tmp_path)
    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]

    result = patch_tool.invoke(_patch_inv(tmp_path, "f.txt", base_sha256=base))
    assert result.status == "failed"
    assert result.error.code == "MISSING_PATCH_CONTENT"


def test_missing_replacement(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hi")
    read_tool, patch_tool, *_ = _build(tmp_path)
    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]

    result = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, search="hi")
    )
    assert result.status == "failed"
    assert result.error.code == "MISSING_REPLACEMENT"


def test_hash_mismatch_when_file_changed_on_disk(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("v1\n")
    read_tool, patch_tool, *_ = _build(tmp_path)
    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]

    # External change between read and patch
    target.write_text("v2\n")

    result = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, new_content="v3\n")
    )
    assert result.status == "failed"
    assert result.error.code == "HASH_MISMATCH_FILE_CHANGED"
    # File on disk MUST NOT have been touched
    assert target.read_text() == "v2\n"


def test_search_not_found(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("hello world\n")
    read_tool, patch_tool, *_ = _build(tmp_path)
    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]

    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=base,
            search="missing string",
            replacement="x",
        )
    )
    assert result.status == "failed"
    assert result.error.code == "SEARCH_NOT_FOUND"
    assert target.read_text() == "hello world\n"


def test_ambiguous_search_without_replace_all(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("foo foo foo\n")
    read_tool, patch_tool, *_ = _build(tmp_path)
    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]

    result = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=base,
            search="foo",
            replacement="bar",
        )
    )
    assert result.status == "failed"
    assert result.error.code == "AMBIGUOUS_SEARCH"
    # File untouched
    assert target.read_text() == "foo foo foo\n"


# ---------------------------------------------------------------------------#
# read_before_write predicate
# ---------------------------------------------------------------------------#


def test_read_before_write_predicate_registered():
    assert "read_before_write" in PREDICATES


def test_read_before_write_denies_when_no_receipt(tmp_path):
    fn = PREDICATES["read_before_write"]
    target = tmp_path / "f.txt"
    target.write_text("x")
    receipts_dir = tmp_path / ".receipts"
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    receipts = ReceiptManager(receipts_dir, clock)

    kernel = MagicMock()
    kernel.workspace = tmp_path
    kernel.receipts = receipts
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="patch_file",
        tool_version="1.0.0",
        input={"path": "f.txt", "base_sha256": "sha256:00"},
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=kernel)
    out = fn(ctx)
    assert out.decision is PolicyDecisionKind.DENY
    assert out.rejection.reason_code == "MUST_READ_BEFORE_WRITE"


def test_read_before_write_allows_when_receipt_exists(tmp_path):
    fn = PREDICATES["read_before_write"]
    target = tmp_path / "f.txt"
    target.write_text("x")
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    receipts = ReceiptManager(tmp_path / ".receipts", clock)
    receipts.record_file_read(
        task_id="t1",
        path=str(target.resolve()),
        content="x",
        bytes_read=1,
        file_sha256="sha256:abc",
    )

    kernel = MagicMock()
    kernel.workspace = tmp_path
    kernel.receipts = receipts
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="patch_file",
        tool_version="1.0.0",
        input={"path": "f.txt", "base_sha256": "sha256:abc"},
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=kernel)
    assert fn(ctx).decision.value == "allow"


def test_read_before_write_allows_for_read_only_tools(tmp_path):
    fn = PREDICATES["read_before_write"]
    kernel = MagicMock()
    kernel.workspace = tmp_path
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="read_file",
        tool_version="1.0.0",
        input={"path": "f.txt"},
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=kernel)
    assert fn(ctx).decision.value == "allow"


# ---------------------------------------------------------------------------#
# hash_matches_receipt predicate
# ---------------------------------------------------------------------------#


def test_hash_matches_receipt_predicate_registered():
    assert "hash_matches_receipt" in PREDICATES


def test_hash_matches_receipt_allows_when_match(tmp_path):
    fn = PREDICATES["hash_matches_receipt"]
    target = tmp_path / "f.txt"
    target.write_text("x")
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    receipts = ReceiptManager(tmp_path / ".receipts", clock)
    receipts.record_file_read(
        task_id="t1",
        path=str(target.resolve()),
        content="x",
        bytes_read=1,
        file_sha256="sha256:abc",
    )

    kernel = MagicMock()
    kernel.workspace = tmp_path
    kernel.receipts = receipts
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="patch_file",
        tool_version="1.0.0",
        input={"path": "f.txt", "base_sha256": "sha256:abc"},
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=kernel)
    assert fn(ctx).decision.value == "allow"


def test_hash_matches_receipt_denies_when_mismatch(tmp_path):
    fn = PREDICATES["hash_matches_receipt"]
    target = tmp_path / "f.txt"
    target.write_text("x")
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    receipts = ReceiptManager(tmp_path / ".receipts", clock)
    receipts.record_file_read(
        task_id="t1",
        path=str(target.resolve()),
        content="x",
        bytes_read=1,
        file_sha256="sha256:expected",
    )

    kernel = MagicMock()
    kernel.workspace = tmp_path
    kernel.receipts = receipts
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="patch_file",
        tool_version="1.0.0",
        input={"path": "f.txt", "base_sha256": "sha256:wrong"},
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=kernel)
    out = fn(ctx)
    assert out.decision is PolicyDecisionKind.DENY
    assert out.rejection.reason_code == "HASH_MISMATCH_FILE_CHANGED"
    assert "sha256:expected" in out.rejection.message


def test_hash_matches_receipt_passes_when_no_receipt(tmp_path):
    """When there's no receipt, read_before_write handles it; this predicate allows."""
    fn = PREDICATES["hash_matches_receipt"]
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    receipts = ReceiptManager(tmp_path / ".receipts", clock)

    kernel = MagicMock()
    kernel.workspace = tmp_path
    kernel.receipts = receipts
    action = ActionRequest(
        kind="tool_invocation",
        actor="planner",
        task_id="t1",
        tool_name="patch_file",
        tool_version="1.0.0",
        input={"path": "f.txt", "base_sha256": "sha256:abc"},
    )
    ctx = PredicateContext(action=action, task_state=MagicMock(), kernel=kernel)
    assert fn(ctx).decision.value == "allow"


# ---------------------------------------------------------------------------#
# restore_file
# ---------------------------------------------------------------------------#


def test_restore_file_reverts_a_patch(tmp_path):
    target = tmp_path / "f.txt"
    original = "ORIGINAL\n"
    target.write_text(original)
    read_tool, patch_tool, restore_tool, *_ = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    presult = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, new_content="MUTATED\n")
    )
    backup_id = presult.output["backup_artifact_id"]
    assert target.read_text() == "MUTATED\n"

    rresult = restore_tool.invoke(_restore_inv(backup_id, reason="verifier failed"))
    assert rresult.status == "success"
    assert target.read_text() == original
    assert rresult.output["path"] == str(target)
    assert rresult.output["bytes_restored"] == len(original.encode())


def test_restore_file_unknown_backup(tmp_path):
    _, _, restore_tool, *_ = _build(tmp_path)
    result = restore_tool.invoke(_restore_inv("art_unknown"))
    assert result.status == "failed"
    assert result.error.code == "BACKUP_NOT_FOUND"


def test_restore_file_updates_receipt(tmp_path):
    target = tmp_path / "f.txt"
    original = "ORIGINAL\n"
    target.write_text(original)
    read_tool, patch_tool, restore_tool, receipts, *_ = _build(tmp_path)

    base = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    presult = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base, new_content="X\n")
    )
    backup_id = presult.output["backup_artifact_id"]

    rresult = restore_tool.invoke(_restore_inv(backup_id))
    receipt = receipts.get_file_read("task_test", str(target.resolve()))
    assert receipt is not None
    assert receipt.file_sha256 == rresult.output["sha256_after"]
    # And that hash equals the original file hash
    assert receipt.file_sha256 == _hash(original.encode())


# ---------------------------------------------------------------------------#
# End-to-end iterative flow: read → patch → re-read → patch
# ---------------------------------------------------------------------------#


def test_iterative_patch_requires_fresh_read(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("v1\n")
    read_tool, patch_tool, *_ = _build(tmp_path)

    # First patch
    base1 = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    p1 = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base1, new_content="v2\n")
    )
    assert p1.status == "success"
    new_hash = p1.output["sha256_after"]

    # Second patch with the OLD base_sha256 must fail
    p2_bad = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=base1,
            new_content="v3\n",
            invocation_id="inv_patch_2_bad",
        )
    )
    assert p2_bad.status == "failed"
    assert p2_bad.error.code == "HASH_MISMATCH_FILE_CHANGED"

    # Second patch with the NEW base_sha256 (from the first patch's output) succeeds
    p2_good = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=new_hash,
            new_content="v3\n",
            invocation_id="inv_patch_2_good",
        )
    )
    assert p2_good.status == "success"
    assert target.read_text() == "v3\n"


def test_patch_then_restore_then_patch_again(tmp_path):
    """A full round-trip: patch → restore → patch with the restored hash."""
    target = tmp_path / "f.txt"
    target.write_text("A\n")
    read_tool, patch_tool, restore_tool, *_ = _build(tmp_path)

    base_a = read_tool.invoke(_read_inv(tmp_path, "f.txt")).output["file_sha256"]
    p1 = patch_tool.invoke(
        _patch_inv(tmp_path, "f.txt", base_sha256=base_a, new_content="B\n")
    )
    backup = p1.output["backup_artifact_id"]
    assert target.read_text() == "B\n"

    rresult = restore_tool.invoke(_restore_inv(backup))
    restored_hash = rresult.output["sha256_after"]
    assert restored_hash == base_a  # back to original
    assert target.read_text() == "A\n"

    # Now patch again with the restored hash
    p2 = patch_tool.invoke(
        _patch_inv(
            tmp_path,
            "f.txt",
            base_sha256=restored_hash,
            new_content="C\n",
            invocation_id="inv_patch_2",
        )
    )
    assert p2.status == "success"
    assert target.read_text() == "C\n"


def test_patch_capabilities_and_risk(tmp_path):
    _, patch_tool, restore_tool, *_ = _build(tmp_path)
    assert patch_tool.manifest.risk_level == "mutating"
    assert patch_tool.manifest.side_effects is True
    assert "fs.write" in patch_tool.manifest.capabilities
    assert restore_tool.manifest.risk_level == "mutating"
    assert "fs.restore" in restore_tool.manifest.capabilities
