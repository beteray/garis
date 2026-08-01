"""OpenAI chat-completions adapter."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ...errors import ProviderError
from ...net import HttpClient
from ..base import (
    Capability,
    Completion,
    Job,
    Message,
    ModelSpec,
    Role,
    ToolCall,
    Usage,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# Model ids and prices live here on purpose: when a vendor renames a model or
# changes pricing, exactly one file changes and no behaviour above it moves.
MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="gpt-4.1",
        provider="openai",
        jobs=frozenset({Job.PLAN, Job.REASON, Job.CODE, Job.CHAT, Job.SUMMARIZE,
                        Job.EXTRACT, Job.VISION}),
        capabilities=frozenset({Capability.TOOLS, Capability.VISION,
                                Capability.JSON_MODE, Capability.STREAMING}),
        quality=0.86, speed=0.6, input_cost=2.0, output_cost=8.0, context_tokens=1_000_000,
    ),
    ModelSpec(
        name="gpt-4.1-mini",
        provider="openai",
        jobs=frozenset({Job.CHAT, Job.CLASSIFY, Job.SUMMARIZE, Job.EXTRACT, Job.VISION}),
        capabilities=frozenset({Capability.TOOLS, Capability.VISION,
                                Capability.JSON_MODE, Capability.STREAMING}),
        quality=0.68, speed=0.9, input_cost=0.4, output_cost=1.6, context_tokens=1_000_000,
    ),
    ModelSpec(
        name="o3",
        provider="openai",
        jobs=frozenset({Job.REASON, Job.PLAN, Job.CODE}),
        capabilities=frozenset({Capability.TOOLS, Capability.JSON_MODE}),
        quality=0.94, speed=0.25, input_cost=2.0, output_cost=8.0, context_tokens=200_000,
    ),
    ModelSpec(
        name="gpt-4o-realtime-preview",
        provider="openai",
        # VOICE only — see the note on Gemini's native-audio model.
        jobs=frozenset({Job.VOICE}),
        capabilities=frozenset({Capability.REALTIME_VOICE, Capability.STREAMING,
                                Capability.TOOLS}),
        quality=0.7, speed=0.95, input_cost=5.0, output_cost=20.0, context_tokens=128_000,
    ),
)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str | None,
        *,
        http: HttpClient | None = None,
        base_url: str = "",
        models: Sequence[ModelSpec] = MODELS,
    ) -> None:
        self._key = api_key
        self._http = http or HttpClient()
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._models = tuple(models)

    def models(self) -> Sequence[ModelSpec]:
        return self._models

    def available(self) -> bool:
        return bool(self._key)

    def health_request(self) -> tuple[str, dict[str, str]] | None:
        """Listing models costs nothing and still proves the key opens the door."""
        if not self._key:
            return None
        return f"{self._base}/models", self._headers()

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise ProviderError("Brak klucza OpenAI", retryable=False)
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    async def complete(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
        timeout: float = 120.0,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model.name,
            "messages": [_encode(m) for m in messages],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        resp = await self._http.post(
            f"{self._base}/chat/completions",
            headers=self._headers(),
            json_body=body,
            timeout=timeout,
        )
        data = resp.raise_for_status().json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Nieoczekiwana odpowiedź OpenAI: {data}") from exc

        calls = tuple(
            ToolCall(
                id=call.get("id", uuid.uuid4().hex),
                name=call["function"]["name"],
                arguments=_load_args(call["function"].get("arguments", "{}")),
            )
            for call in message.get("tool_calls") or []
        )
        usage_raw = data.get("usage") or {}
        usage = Usage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
        )
        usage.cost = model.price_for(usage.input_tokens, usage.output_tokens)
        return Completion(
            text=message.get("content") or "",
            model=model.name,
            provider=self.name,
            tool_calls=calls,
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            raw=data,
        )

    async def stream(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> AsyncIterator[str]:
        body = {
            "model": model.name,
            "messages": [_encode(m) for m in messages],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "stream": True,
        }
        async for chunk in self._http.stream_sse(
            f"{self._base}/chat/completions",
            headers=self._headers(),
            json_body=body,
            timeout=timeout,
        ):
            for choice in chunk.get("choices", []):
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece


def _encode(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if message.role is Role.ASSISTANT and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ],
        }
    if message.images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            encoded = base64.b64encode(image).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        return {"role": message.role.value, "content": parts}
    return {"role": message.role.value, "content": message.content}


def _load_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["MODELS", "OpenAIProvider"]
