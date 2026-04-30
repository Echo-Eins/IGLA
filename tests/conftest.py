"""Shared fixtures."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from igla.config import IglaSettings, LMStudioSettings, PathsSettings, PlannerSettings
from igla.kernel.clock import StepClock
from igla.kernel.kernel import Kernel
from igla.motivation.cycle import MotivationCycle
from igla.motivation.rule import load_rules
from igla.planner.llm_client import OfflineCannedClient
from igla.planner.planner import Planner
from igla.policies.constitution import load_constitution
from igla.policies.engine import PolicyContext, PolicyEngine
from igla.todo.store import TodoStore
from igla.tools.builtin import (
    AskUserTool,
    FindFilesTool,
    NoopObserveTool,
    ReadFileTool,
    SearchTextTool,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"


class ScriptedAskUserChannel:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self._questions: list[str] = []

    def ask(self, *, question: str, prompt_label: str | None = None) -> str:
        self._questions.append(question)
        if not self._answers:
            return ""
        return self._answers.pop(0)


@pytest.fixture
def make_settings(tmp_path: Path):
    def _factory() -> IglaSettings:
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        state = workspace / ".igla"
        paths = PathsSettings(
            workspace=workspace,
            state_dir=state,
            events_file=state / "events.jsonl",
            artifacts_dir=state / "artifacts",
            receipts_dir=state / "receipts",
            todo_dir=state / "todo",
            constitution_file=CONFIGS / "constitution.yaml",
            motivation_file=CONFIGS / "motivation.yaml",
        )
        return IglaSettings(
            paths=paths,
            lm_studio=LMStudioSettings(),
            planner=PlannerSettings(
                max_iterations_per_task=20,
                max_consecutive_rejections=3,
                max_clarification_depth=4,
            ),
        )

    return _factory


@pytest.fixture
def step_clock() -> StepClock:
    return StepClock(start=datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc), step_seconds=1.0)


def build_runtime(
    settings: IglaSettings,
    *,
    canned_responses: list[dict],
    answers: list[str] | None = None,
    clock=None,
) -> tuple[Planner, Kernel, TodoStore, ScriptedAskUserChannel, OfflineCannedClient]:
    kernel = Kernel(settings, clock=clock)
    channel = ScriptedAskUserChannel(answers or [])
    kernel.registry.register_many(
        [
            AskUserTool(channel=channel),
            FindFilesTool(workspace_root=str(settings.paths.workspace)),
            ReadFileTool(
                workspace_root=str(settings.paths.workspace),
                receipts=kernel.receipts,
            ),
            SearchTextTool(workspace_root=str(settings.paths.workspace)),
            NoopObserveTool(),
        ]
    )
    constitution = load_constitution(settings.paths.constitution_file)
    policy = PolicyEngine(
        constitution,
        PolicyContext(
            iteration_limit=settings.planner.max_iterations_per_task,
            clarification_depth_limit=settings.planner.max_clarification_depth,
            consecutive_rejections_limit=settings.planner.max_consecutive_rejections,
        ),
    )
    rules = load_rules(settings.paths.motivation_file)
    motivation = MotivationCycle(rules, kernel)
    todo_store = TodoStore(settings.paths.todo_dir)
    llm = OfflineCannedClient(canned_responses)
    planner = Planner(
        kernel=kernel,
        settings=settings,
        policy=policy,
        motivation=motivation,
        todo_store=todo_store,
        llm=llm,
    )
    return planner, kernel, todo_store, channel, llm
