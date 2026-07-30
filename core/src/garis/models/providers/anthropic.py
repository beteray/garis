"""Anthropic (Claude) adapter."""

from __future__ import annotations

import base64
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

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="claude-opus-5",
        provider="anthropic",
        jobs=frozenset({Job.PLAN, Job.REASON, Job.CODE, Job.CHAT, Job.SUMMARIZE,
                        Job.EXTRACT, Job.VISION}),
        capabilities=frozenset({Capability.TOOLS, Capability.VISION,
                                Capability.STREAMING, Capability.LONG_CONTEXT}),
        quality=0.96, speed=0.5, input_cost=5.0, output_cost=25.0, context_tokens=200_000,
    ),
    ModelSpec(
        name="claude-sonnet-5",
        provider="anthropic",
        jobs=frozenset({Job.PLAN, Job.REASON, Job.CODE, Job.CHAT, Job.SUMMARIZE,
                        Job.EXTRACT, Job.VISION}),
        capabilities=frozenset({Capability.TOOLS, Capability.VISION,
                                Capability.STREAMING, Capability.LONG_CONTEXT}),
        quality=0.9, speed=0.72, input_cost=3.0, output_cost=15.0, context_tokens=200_000,
    ),
    ModelSpec(
        name="claude-haiku-4-5-20251001",
        provider="anthropic",
        jobs=frozenset({Job.CHAT, Job.CLASSIFY, Job.SUMMARIZE, Job.EXTRACT}),
        capabilities=frozenset({Capability.TOOLS, Capability.STREAMING}),
        quality=0.7, speed=0.95, input_cost=1.0, output_cost=5.0, context_tokens=200_000,
    ),
)


class AnthropicProvider:
    name = "anthropic"

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

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise ProviderError("Brak klucza Anthropic", retryable=False)
        return {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
        }

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
        system, converted = _split_system(messages)
        body: dict[str, Any] = {
            "model": model.name,
            "messages": converted,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        if json_mode:
            # No dedicated JSON mode; a final instruction is the supported route.
            body["messages"] = converted + [
                {"role": "assistant", "content": "{"}
            ]

        resp = await self._http.post(
            f"{self._base}/messages", headers=self._headers(), json_body=body, timeout=timeout
        )
        data = resp.raise_for_status().json()
        if data.get("type") == "error":
            raise ProviderError(f"Anthropic: {data.get('error', {}).get('message', data)}")

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.get("id", uuid.uuid4().hex),
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )
        text = "".join(text_parts)
        if json_mode and text and not text.lstrip().startswith("{"):
            text = "{" + text  # we prefilled the opening brace

        usage_raw = data.get("usage") or {}
        usage = Usage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
        )
        usage.cost = model.price_for(usage.input_tokens, usage.output_tokens)
        return Completion(
            text=text,
            model=model.name,
            provider=self.name,
            tool_calls=tuple(calls),
            usage=usage,
            finish_reason=data.get("stop_reason", "stop"),
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
        system, converted = _split_system(messages)
        body: dict[str, Any] = {
            "model": model.name,
            "messages": converted,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            body["system"] = system
        async for chunk in self._http.stream_sse(
            f"{self._base}/messages", headers=self._headers(), json_body=body, timeout=timeout
        ):
            if chunk.get("type") == "content_block_delta":
                piece = (chunk.get("delta") or {}).get("text")
                if piece:
                    yield piece


def _split_system(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Claude takes the system prompt out of band."""
    system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            continue
        if message.role is Role.TOOL:
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
            continue
        if message.role is Role.ASSISTANT and message.tool_calls:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue
        if message.images:
            blocks = [{"type": "text", "text": message.content}]
            for image in message.images:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(image).decode("ascii"),
                        },
                    }
                )
            converted.append({"role": "user", "content": blocks})
            continue
        converted.append({"role": message.role.value, "content": message.content})
    return "\n\n".join(p for p in system_parts if p), converted


__all__ = ["MODELS", "AnthropicProvider"]
