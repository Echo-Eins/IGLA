"""LM Studio (OpenAI-compatible) client + an offline test client.

Notes on LM Studio:
* It speaks the OpenAI Chat Completions API at ``<base_url>/chat/completions``.
* It supports ``response_format = {"type": "json_schema", "json_schema": ...}``
  on most builds; if the local model does not, set
  ``IglaSettings.lm_studio.use_json_schema_response = False`` and rely on a
  prompt instructing the model to return a single JSON object.

We deliberately keep the request narrow:
* messages: list of ``LLMChatMessage`` (system/user/assistant).
* response_format: optional JSON Schema.
* temperature/top_p/max_tokens from settings.
* No streaming. No tools. No function-calling. The structured output IS the
  proposal envelope; the planner parses it directly.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class LLMChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMClient(Protocol):
    def complete_json(
        self,
        *,
        messages: Iterable[LLMChatMessage],
        json_schema: dict[str, Any] | None,
        schema_name: str = "PlannerProposal",
    ) -> dict[str, Any]: ...


class LMStudioError(RuntimeError):
    pass


class LMStudioClient:
    """Sync HTTP client for LM Studio's OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 120.0,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 2048,
        use_json_schema_response: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._use_schema = use_json_schema_response
        self._client = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LMStudioClient":
        return self

    def __exit__(self, *exc) -> None:  # pragma: no cover
        self.close()

    def complete_json(
        self,
        *,
        messages: Iterable[LLMChatMessage],
        json_schema: dict[str, Any] | None,
        schema_name: str = "PlannerProposal",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if json_schema is not None and self._use_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
        elif json_schema is not None:
            # Fallback: ask the model to emit raw JSON; the planner enforces parsing.
            payload["response_format"] = {"type": "json_object"}

        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LMStudioError(f"transport error talking to {url}: {exc}") from exc
        if response.status_code >= 400:
            raise LMStudioError(
                f"LM Studio returned {response.status_code}: {response.text[:500]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise LMStudioError(f"non-JSON response from LM Studio: {response.text[:500]}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LMStudioError(f"unexpected response shape: {data!r}") from exc
        return _parse_json_strict(content)


def _parse_json_strict(content: str) -> dict[str, Any]:
    """Parse the model's JSON output.

    LM Studio sometimes returns plain text wrapped in code fences (``‍```json
    ... ```) when the model is not perfectly tuned for structured outputs. We
    strip a single set of fences if present, then ``json.loads``.
    """
    text = content.strip()
    if text.startswith("```"):
        # ```json\n...\n```
        text = text.lstrip("`")
        # remove a leading "json" tag if present
        if text.lstrip().startswith("json\n"):
            text = text.split("json\n", 1)[1]
        elif text.lstrip().startswith("json "):
            text = text.split("json ", 1)[1]
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LMStudioError(f"model output is not valid JSON: {content[:500]}") from exc
    if not isinstance(parsed, dict):
        raise LMStudioError(f"model output JSON is not an object: {content[:200]}")
    return parsed


# ---------------------------------------------------------------------------
#  Offline canned client — test/dev only
# ---------------------------------------------------------------------------


class OfflineCannedClient:
    """Returns a queue of pre-baked JSON responses for tests / dry-runs."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._queue: list[dict[str, Any]] = list(responses)
        self._sent: list[list[LLMChatMessage]] = []

    @property
    def sent_messages(self) -> list[list[LLMChatMessage]]:
        return self._sent

    def complete_json(
        self,
        *,
        messages: Iterable[LLMChatMessage],
        json_schema: dict[str, Any] | None,
        schema_name: str = "PlannerProposal",
    ) -> dict[str, Any]:
        del json_schema, schema_name
        self._sent.append(list(messages))
        if not self._queue:
            raise LMStudioError("OfflineCannedClient ran out of responses")
        return self._queue.pop(0)
