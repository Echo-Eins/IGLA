"""LLM clients + an offline test client.

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

Notes on Ollama:
* The native endpoint is ``<base_url>/api/chat``.
* Streaming is disabled with ``stream=false`` so the planner gets one JSON
  response object.
* Structured outputs use Ollama's ``format`` field. It accepts either
  ``"json"`` or a JSON Schema object.
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

    def close(self) -> None: ...


class LMStudioError(RuntimeError):
    pass


class OllamaError(RuntimeError):
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
        repeat_penalty: float = 1.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._use_schema = use_json_schema_response
        self._repeat_penalty = repeat_penalty
        self._client = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LMStudioClient:
        return self

    def __exit__(self, *exc: object) -> None:  # pragma: no cover
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
        if self._repeat_penalty != 1.0:
            # llama.cpp extension (not in the OpenAI spec). Accepted by LM Studio
            # and most llama.cpp-based servers. Penalises recently-seen tokens to
            # prevent degenerate repetition loops after policy rejections.
            payload["repeat_penalty"] = self._repeat_penalty
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


class OllamaClient:
    """Sync HTTP client for Ollama's native chat endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 120.0,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 2048,
        use_json_schema_response: bool = True,
        repeat_penalty: float = 1.0,
        keep_alive: str = "30m",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens
        self._use_schema = use_json_schema_response
        self._repeat_penalty = repeat_penalty
        self._keep_alive = keep_alive
        self._client = http_client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *exc: object) -> None:  # pragma: no cover
        self.close()

    def complete_json(
        self,
        *,
        messages: Iterable[LLMChatMessage],
        json_schema: dict[str, Any] | None,
        schema_name: str = "PlannerProposal",
    ) -> dict[str, Any]:
        del schema_name
        options: dict[str, Any] = {
            "temperature": self._temperature,
            "top_p": self._top_p,
            "num_predict": self._max_tokens,
        }
        if self._repeat_penalty != 1.0:
            options["repeat_penalty"] = self._repeat_penalty

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": options,
        }
        if self._keep_alive:
            payload["keep_alive"] = self._keep_alive
        if json_schema is not None and self._use_schema:
            payload["format"] = json_schema
        elif json_schema is not None:
            payload["format"] = "json"

        url = f"{self._base_url}/api/chat"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise OllamaError(f"transport error talking to {url}: {exc}") from exc
        if response.status_code >= 400:
            raise OllamaError(f"Ollama returned {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError(f"non-JSON response from Ollama: {response.text[:500]}") from exc
        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaError(f"unexpected response shape: {data!r}") from exc
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            raise OllamaError(f"Ollama message content is not text/JSON: {content!r}")
        try:
            return _parse_json_strict(content)
        except LMStudioError as exc:
            raise OllamaError(str(exc)) from exc


def _parse_json_strict(content: str) -> dict[str, Any]:
    """Parse the model's JSON output.

    Tolerates two common server-side template glitches:

    * Code-fenced output: ``‍```json\n{...}\n``` `` — strips a single fence.
    * Harmony channel markers leaking through (gpt-oss models): the assistant
      output looks like ``<|channel|>final <|constrain|>json<|message|>{...}``
      because LM Studio applied a generic ChatML template instead of the
      gpt-oss Harmony template. We extract the JSON object from the first ``{``
      to the matching closing brace.
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
    if "<|" in text and "{" in text:
        # Harmony-style prefix leaked through. Slice to the first '{' and to the
        # matching closing '}' (depth-aware so nested objects survive).
        text = _extract_first_json_object(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LMStudioError(f"model output is not valid JSON: {content[:500]}") from exc
    if not isinstance(parsed, dict):
        raise LMStudioError(f"model output JSON is not an object: {content[:200]}")
    return parsed


def _extract_first_json_object(text: str) -> str:
    """Return the substring from the first '{' to its matching '}'.

    Quote-aware so braces inside strings don't fool the depth counter.
    """
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


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

    def close(self) -> None:
        return None

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
