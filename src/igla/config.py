"""IGLA runtime configuration.

Configuration is intentionally explicit and code-driven. Anything that affects
policy or schema goes through Pydantic models, never plain dicts.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LMStudioSettings(BaseModel):
    """Configuration for the LM Studio (or any OpenAI-compatible) server."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = "http://127.0.0.1:1234/v1"
    api_key: str = "lm-studio"
    model: str = "local-model"
    request_timeout_s: float = 120.0
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 2048
    # When False the planner falls back to plain JSON parsing instead of
    # the OpenAI-style ``response_format=json_schema`` request.
    use_json_schema_response: bool = True


class PathsSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: Path
    state_dir: Path  # `.igla/`
    events_file: Path  # `.igla/events.jsonl`
    artifacts_dir: Path
    receipts_dir: Path
    todo_dir: Path
    constitution_file: Path
    motivation_file: Path

    @classmethod
    def from_workspace(cls, workspace: Path, *, package_root: Path) -> "PathsSettings":
        state_dir = (workspace / ".igla").resolve()
        configs = (package_root / "configs").resolve()
        return cls(
            workspace=workspace.resolve(),
            state_dir=state_dir,
            events_file=state_dir / "events.jsonl",
            artifacts_dir=state_dir / "artifacts",
            receipts_dir=state_dir / "receipts",
            todo_dir=state_dir / "todo",
            constitution_file=configs / "constitution.yaml",
            motivation_file=configs / "motivation.yaml",
        )


class PlannerSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_iterations_per_task: int = 60
    max_consecutive_rejections: int = 5
    max_clarification_depth: int = 4


class IglaSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paths: PathsSettings
    lm_studio: LMStudioSettings = Field(default_factory=LMStudioSettings)
    planner: PlannerSettings = Field(default_factory=PlannerSettings)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def package_root() -> Path:
    """Repository root (one above the ``src/`` parent of this module)."""
    return Path(__file__).resolve().parent.parent.parent


def load_settings(workspace: Path | None = None) -> IglaSettings:
    """Build settings from CWD/env. No global mutable state."""
    ws = workspace or Path(os.environ.get("IGLA_WORKSPACE", os.getcwd())).resolve()
    paths = PathsSettings.from_workspace(ws, package_root=package_root())
    lm = LMStudioSettings(
        base_url=os.environ.get("IGLA_LMSTUDIO_URL", "http://127.0.0.1:1234/v1"),
        api_key=os.environ.get("IGLA_LMSTUDIO_KEY", "lm-studio"),
        model=os.environ.get("IGLA_LMSTUDIO_MODEL", "local-model"),
    )
    return IglaSettings(paths=paths, lm_studio=lm)
