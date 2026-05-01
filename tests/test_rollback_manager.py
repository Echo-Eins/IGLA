"""Tests for the RollbackManager service."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from igla.kernel.artifact_store import ArtifactStore
from igla.kernel.clock import StepClock
from igla.kernel.errors import ArtifactStoreError
from igla.kernel.kernel import Kernel
from igla.kernel.rollback_manager import RollbackError, RollbackManager


def _mgr(tmp_path: Path) -> RollbackManager:
    artifacts_dir = tmp_path / ".igla" / "artifacts"
    clock = StepClock(start=datetime(2026, 5, 1, tzinfo=timezone.utc))
    artifacts = ArtifactStore(artifacts_dir, clock)
    return RollbackManager(artifacts, clock)


# ---------------------------------------------------------------------------#
# snapshot()
# ---------------------------------------------------------------------------#


def test_snapshot_creates_artifact_with_metadata(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"hello world")

    snap = mgr.snapshot(path=target, task_id="t1", step_id="s1")

    assert snap.snapshot_id == snap.artifact_id
    assert snap.original_path == str(target)
    assert snap.task_id == "t1"
    assert snap.step_id == "s1"
    assert snap.bytes_size == len(b"hello world")
    assert snap.file_sha256.startswith("sha256:")


def test_snapshot_fails_for_missing_file(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "nope.txt"
    with pytest.raises(RollbackError):
        mgr.snapshot(path=target, task_id="t1")


def test_snapshot_fails_for_directory(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "subdir"
    target.mkdir()
    with pytest.raises(RollbackError):
        mgr.snapshot(path=target, task_id="t1")


def test_snapshot_preserves_content_for_restore(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    payload = b"original content"
    target.write_bytes(payload)

    snap = mgr.snapshot(path=target, task_id="t1")

    # Mutate the file
    target.write_bytes(b"mutated")

    # Restore via snapshot
    restored_path, sha = mgr.restore_snapshot(snap.snapshot_id)
    assert restored_path == target
    assert target.read_bytes() == payload


# ---------------------------------------------------------------------------#
# get_snapshot()
# ---------------------------------------------------------------------------#


def test_get_snapshot_round_trip(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"data")

    original_snap = mgr.snapshot(path=target, task_id="t1", step_id="s1")
    fetched = mgr.get_snapshot(original_snap.snapshot_id)
    assert fetched.original_path == original_snap.original_path
    assert fetched.task_id == "t1"
    assert fetched.step_id == "s1"
    assert fetched.file_sha256 == original_snap.file_sha256


def test_get_snapshot_raises_for_unknown_id(tmp_path):
    mgr = _mgr(tmp_path)
    with pytest.raises(ArtifactStoreError):
        mgr.get_snapshot("art_does_not_exist")


# ---------------------------------------------------------------------------#
# plan() / get_plan()
# ---------------------------------------------------------------------------#


def test_plan_registers_and_retrieves(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    snap = mgr.snapshot(path=target, task_id="t1", step_id="s1")

    plan = mgr.plan(step_id="s1", snapshot_ids=[snap.snapshot_id])
    assert plan.plan_id.startswith("rbp_")
    assert plan.step_id == "s1"
    assert plan.snapshot_ids == [snap.snapshot_id]
    assert plan.strategy == "restore_backup"

    fetched = mgr.get_plan(plan.plan_id)
    assert fetched is not None
    assert fetched.plan_id == plan.plan_id


def test_get_plan_returns_none_for_missing(tmp_path):
    mgr = _mgr(tmp_path)
    assert mgr.get_plan("rbp_missing") is None


def test_list_plans_for_step(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    snap = mgr.snapshot(path=target, task_id="t1", step_id="s1")

    p1 = mgr.plan(step_id="s1", snapshot_ids=[snap.snapshot_id])
    p2 = mgr.plan(step_id="s1", snapshot_ids=[snap.snapshot_id])
    p3 = mgr.plan(step_id="s2", snapshot_ids=[snap.snapshot_id])

    s1_plans = mgr.list_plans_for_step("s1")
    s2_plans = mgr.list_plans_for_step("s2")
    assert {p.plan_id for p in s1_plans} == {p1.plan_id, p2.plan_id}
    assert [p.plan_id for p in s2_plans] == [p3.plan_id]


# ---------------------------------------------------------------------------#
# execute()
# ---------------------------------------------------------------------------#


def test_execute_restores_single_snapshot(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"original")

    snap = mgr.snapshot(path=target, task_id="t1", step_id="s1")
    plan = mgr.plan(step_id="s1", snapshot_ids=[snap.snapshot_id])

    target.write_bytes(b"mutated")
    result = mgr.execute(plan.plan_id)

    assert result.success is True
    assert result.error is None
    assert result.restored_paths == [str(target)]
    assert target.read_bytes() == b"original"


def test_execute_restores_multiple_snapshots(tmp_path):
    mgr = _mgr(tmp_path)
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_bytes(b"AAA")
    f2.write_bytes(b"BBB")

    s1 = mgr.snapshot(path=f1, task_id="t1", step_id="s1")
    s2 = mgr.snapshot(path=f2, task_id="t1", step_id="s1")
    plan = mgr.plan(step_id="s1", snapshot_ids=[s1.snapshot_id, s2.snapshot_id])

    f1.write_bytes(b"X")
    f2.write_bytes(b"Y")

    result = mgr.execute(plan.plan_id)
    assert result.success is True
    assert set(result.restored_paths) == {str(f1), str(f2)}
    assert f1.read_bytes() == b"AAA"
    assert f2.read_bytes() == b"BBB"


def test_execute_unknown_plan(tmp_path):
    mgr = _mgr(tmp_path)
    result = mgr.execute("rbp_nope")
    assert result.success is False
    assert result.error == "ROLLBACK_PLAN_NOT_FOUND"
    assert result.restored_paths == []


def test_execute_unsupported_strategy(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    snap = mgr.snapshot(path=target, task_id="t1")

    plan = mgr.plan(
        step_id="s1",
        snapshot_ids=[snap.snapshot_id],
        strategy="git_revert",
    )
    result = mgr.execute(plan.plan_id)
    assert result.success is False
    assert "git_revert" in (result.error or "")
    assert result.failed_snapshot_ids == [snap.snapshot_id]


def test_execute_partial_failure(tmp_path):
    mgr = _mgr(tmp_path)
    f1 = tmp_path / "a.txt"
    f1.write_bytes(b"AAA")

    s1 = mgr.snapshot(path=f1, task_id="t1")
    # Fabricate a plan that references a real and a fake snapshot id.
    plan = mgr.plan(step_id="s1", snapshot_ids=[s1.snapshot_id, "art_fake"])

    f1.write_bytes(b"X")
    result = mgr.execute(plan.plan_id)

    assert result.success is False
    assert "art_fake" in result.failed_snapshot_ids
    assert str(f1) in result.restored_paths
    assert f1.read_bytes() == b"AAA"  # the good one restored


def test_execute_idempotent_on_double_call(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"original")

    snap = mgr.snapshot(path=target, task_id="t1")
    plan = mgr.plan(step_id="s1", snapshot_ids=[snap.snapshot_id])

    target.write_bytes(b"mutated")
    r1 = mgr.execute(plan.plan_id)
    r2 = mgr.execute(plan.plan_id)
    assert r1.success and r2.success
    assert target.read_bytes() == b"original"


# ---------------------------------------------------------------------------#
# discard()
# ---------------------------------------------------------------------------#


def test_discard_removes_plan_but_keeps_artifact(tmp_path):
    mgr = _mgr(tmp_path)
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")

    snap = mgr.snapshot(path=target, task_id="t1")
    plan = mgr.plan(step_id="s1", snapshot_ids=[snap.snapshot_id])

    mgr.discard(plan.plan_id)
    assert mgr.get_plan(plan.plan_id) is None
    # Artifact must still be restorable directly.
    restored_path, _ = mgr.restore_snapshot(snap.snapshot_id)
    assert restored_path == target


def test_discard_unknown_plan_is_noop(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.discard("rbp_nope")  # should not raise


# ---------------------------------------------------------------------------#
# Kernel wiring
# ---------------------------------------------------------------------------#


def test_rollback_attached_to_kernel(make_settings):
    settings = make_settings()
    kernel = Kernel(settings)
    assert isinstance(kernel.rollback, RollbackManager)
    # Same artifact store backs both
    assert kernel.rollback._artifacts is kernel.artifacts
