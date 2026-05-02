"""Planner — turns user intent into typed proposals via an LLM.

Public entry: ``Planner``. Construct it with a kernel + LLM client + policy
engine + motivation cycle + todo store. ``Planner.run_task`` drives one
task end-to-end through the proposal/policy/execution loop.
"""
from .llm_client import (
    LLMChatMessage,
    LLMClient,
    LMStudioClient,
    OfflineCannedClient,
    OllamaClient,
)
from .planner import Planner, PlannerOutcome
from .prompts import (
    build_proposal_messages,
    build_proposal_schema,
    build_session_system_message,
    build_task_system_message,
    build_turn_user_message,
)

__all__ = [
    "LLMChatMessage",
    "LLMClient",
    "LMStudioClient",
    "OfflineCannedClient",
    "OllamaClient",
    "Planner",
    "PlannerOutcome",
    "build_proposal_messages",
    "build_proposal_schema",
    "build_session_system_message",
    "build_task_system_message",
    "build_turn_user_message",
]
