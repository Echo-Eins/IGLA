"""Safe workspace state reset helpers."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


class StateResetError(ValueError):
    """Raised when a configured state directory is unsafe to remove."""


@dataclass(frozen=True)
class StateResetResult:
    workspace: Path
    state_dir: Path
    removed: bool


def reset_workspace_state(*, workspace: Path, state_dir: Path) -> StateResetResult:
    """Delete the `.igla` runtime state directory for one workspace.

    The guardrails are intentionally strict: this helper only removes a
    directory literally configured as `.igla`, and only when the resolved path
    stays inside the resolved workspace root.
    """
    workspace_path = workspace.resolve()
    configured_state_dir = state_dir
    if configured_state_dir.name != ".igla":
        raise StateResetError(f"refusing to remove non-.igla state dir: {configured_state_dir}")

    state_path = configured_state_dir.resolve()
    try:
        state_path.relative_to(workspace_path)
    except ValueError as exc:
        raise StateResetError(
            f"refusing to remove state dir outside workspace: {state_path}"
        ) from exc

    if not configured_state_dir.exists():
        return StateResetResult(
            workspace=workspace_path,
            state_dir=state_path,
            removed=False,
        )
    if configured_state_dir.is_symlink():
        raise StateResetError(f"refusing to remove symlinked state dir: {configured_state_dir}")
    if not configured_state_dir.is_dir():
        raise StateResetError(
            f"refusing to remove non-directory state path: {configured_state_dir}"
        )

    shutil.rmtree(state_path)
    return StateResetResult(
        workspace=workspace_path,
        state_dir=state_path,
        removed=True,
    )
