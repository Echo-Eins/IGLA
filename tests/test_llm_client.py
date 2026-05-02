"""Parser robustness tests for LM Studio responses."""
from __future__ import annotations

import json

import httpx
import pytest

from igla.planner.llm_client import (
    LLMChatMessage,
    LMStudioError,
    OllamaClient,
    OllamaError,
    _parse_json_strict,
)


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


def test_ollama_client_posts_native_chat_with_schema() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": '{"action":"declare_task_done","reason":"ok","summary":"done"}',
                }
            },
        )

    client = OllamaClient(
        base_url="http://127.0.0.1:11434",
        api_key="secret",
        model="qwen2.5-coder:32b",
        temperature=0.2,
        top_p=0.9,
        max_tokens=123,
        repeat_penalty=1.07,
        keep_alive="1h",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    out = client.complete_json(
        messages=[
            LLMChatMessage(role="system", content="role"),
            LLMChatMessage(role="user", content="turn"),
        ],
        json_schema={"type": "object", "properties": {"action": {"type": "string"}}},
    )

    assert out["summary"] == "done"
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen2.5-coder:32b"
    assert payload["stream"] is False
    assert payload["keep_alive"] == "1h"
    assert payload["format"] == {"type": "object", "properties": {"action": {"type": "string"}}}
    assert payload["messages"] == [
        {"role": "system", "content": "role"},
        {"role": "user", "content": "turn"},
    ]
    assert payload["options"] == {
        "temperature": 0.2,
        "top_p": 0.9,
        "num_predict": 123,
        "repeat_penalty": 1.07,
    }
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer secret"


def test_ollama_client_can_fallback_to_json_format() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["format"] == "json"
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": '{"action":"declare_task_done","reason":"ok","summary":"done"}',
                }
            },
        )

    client = OllamaClient(
        base_url="http://localhost:11434",
        model="llama3.1:8b",
        use_json_schema_response=False,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    out = client.complete_json(
        messages=[LLMChatMessage(role="user", content="turn")],
        json_schema={"type": "object"},
    )

    assert out["action"] == "declare_task_done"


def test_ollama_client_reports_http_errors() -> None:
    client = OllamaClient(
        base_url="http://localhost:11434",
        model="missing:model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404, text="missing"))
        ),
    )

    with pytest.raises(OllamaError, match="404"):
        client.complete_json(
            messages=[LLMChatMessage(role="user", content="turn")],
            json_schema={"type": "object"},
        )
