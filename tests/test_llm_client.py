"""Parser robustness tests for LM Studio responses."""
from __future__ import annotations

import pytest

from igla.planner.llm_client import LMStudioError, _parse_json_strict


def test_plain_json() -> None:
    out = _parse_json_strict('{"action":"declare_task_done","reason":"ok","summary":"s"}')
    assert out["action"] == "declare_task_done"


def test_strips_code_fence() -> None:
    raw = '```json\n{"action":"declare_task_done","reason":"ok","summary":"s"}\n```'
    out = _parse_json_strict(raw)
    assert out["action"] == "declare_task_done"


def test_strips_harmony_channel_prefix() -> None:
    """gpt-oss models leak <|channel|>... when chat template is wrong."""
    raw = (
        '<|channel|>final <|constrain|>json<|message|>'
        '{"action":"tool_invocation","reason":"x","tool_name":"find_files",'
        '"tool_version":"1.0.0","input":{"query":"x"}}'
    )
    out = _parse_json_strict(raw)
    assert out["tool_name"] == "find_files"


def test_harmony_with_trailing_end_token() -> None:
    raw = (
        '<|channel|>final <|constrain|>json<|message|>'
        '{"action":"declare_task_done","reason":"ok","summary":"done"}'
        '<|end|>'
    )
    out = _parse_json_strict(raw)
    assert out["summary"] == "done"


def test_handles_braces_inside_strings() -> None:
    raw = (
        '<|channel|>final<|message|>'
        '{"action":"declare_task_done","reason":"got {nested} text",'
        '"summary":"contains } brace"}'
    )
    out = _parse_json_strict(raw)
    assert out["reason"] == "got {nested} text"
    assert out["summary"] == "contains } brace"


def test_invalid_json_raises() -> None:
    with pytest.raises(LMStudioError):
        _parse_json_strict("not json at all")
