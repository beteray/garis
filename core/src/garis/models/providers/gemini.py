"""Google Gemini adapter.

Gemini also carries GARIS's cloud path for real-time voice: the Live API is a
speech-to-speech session with built-in interruption handling, so the voice layer
can use it instead of stitching STT and TTS together. That capability is declared
here (``Capability.REALTIME_VOICE``) and consumed by ``garis.voice``.
"""

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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="gemini-2.5-pro",
        provider="gemini",
        jobs=frozenset({Job.PLAN, Job.REASON, Job.CODE, Job.CHAT, Job.SUMMARIZE,
                        Job.EXTRACT, Job.VISION}),
        capabilities=frozenset({Capability.TOOLS, Capability.VISION, Capability.JSON_MODE,
                                Capability.STREAMING, Capability.LONG_CONTEXT}),
        quality=0.9, speed=0.55, input_cost=1.25, output_cost=10.0, context_tokens=1_000_000,
    ),
    ModelSpec(
        name="gemini-2.5-flash",
        provider="gemini",
        jobs=frozenset({Job.CHAT, Job.CLASSIFY, Job.SUMMARIZE, Job.EXTRACT, Job.VISION,
                        Job.PLAN}),
        capabilities=frozenset({Capability.TOOLS, Capability.VISION, Capability.JSON_MODE,
                                Capability.STREAMING, Capability.LONG_CONTEXT}),
        quality=0.74, speed=0.93, input_cost=0.3, output_cost=2.5, context_tokens=1_000_000,
    ),
    ModelSpec(
        name="gemini-2.5-flash-native-audio-preview",
        provider="gemini",
        # VOICE only. Declaring CHAT as well made this preview model win the
        # ranking for ordinary typed conversation — measured, and wrong: a
        # speech-to-speech session is a different modality, not a cheaper chat.
        jobs=frozenset({Job.VOICE}),
        capabilities=frozenset({Capability.REALTIME_VOICE, Capability.STREAMING,
                                Capability.TOOLS}),
        quality=0.72, speed=0.96, input_cost=0.5, output_cost=2.0, context_tokens=128_000,
    ),
)


class GeminiProvider:
    name = "gemini"

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
        return f"{self._base}/models?key={self._key}", {}

    def _url(self, model: ModelSpec, method: str) -> str:
        if not self._key:
            raise ProviderError("Brak klucza Gemini", retryable=False)
        return f"{self._base}/models/{model.name}:{method}?key={self._key}"

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
        system, contents = _convert(messages)
        generation: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if json_mode:
            generation["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": _clean_schema(
                                t.get("parameters", {"type": "object", "properties": {}})
                            ),
                        }
                        for t in tools
                    ]
                }
            ]

        resp = await self._http.post(
            self._url(model, "generateContent"),
            headers={"Content-Type": "application/json"},
            json_body=body,
            timeout=timeout,
        )
        data = resp.raise_for_status().json()
        if "error" in data:
            raise ProviderError(f"Gemini: {data['error'].get('message', data['error'])}")

        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "brak odpowiedzi")
            raise ProviderError(f"Gemini nie zwróciło treści ({reason})")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fn = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=uuid.uuid4().hex,
                        name=fn.get("name", ""),
                        arguments=fn.get("args") or {},
                    )
                )

        usage_raw = data.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=usage_raw.get("promptTokenCount", 0),
            output_tokens=usage_raw.get("candidatesTokenCount", 0),
        )
        usage.cost = model.price_for(usage.input_tokens, usage.output_tokens)
        return Completion(
            text="".join(text_parts),
            model=model.name,
            provider=self.name,
            tool_calls=tuple(calls),
            usage=usage,
            finish_reason=candidates[0].get("finishReason", "stop"),
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
        system, contents = _convert(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = self._url(model, "streamGenerateContent") + "&alt=sse"
        async for chunk in self._http.stream_sse(
            url, headers={"Content-Type": "application/json"}, json_body=body, timeout=timeout
        ):
            for candidate in chunk.get("candidates", []):
                for part in (candidate.get("content") or {}).get("parts", []):
                    if part.get("text"):
                        yield part["text"]


def _convert(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            continue
        if message.role is Role.TOOL:
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": message.name,
                                "response": {"result": message.content},
                            }
                        }
                    ],
                }
            )
            continue
        role = "model" if message.role is Role.ASSISTANT else "user"
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for call in message.tool_calls:
            parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
        for image in message.images:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(image).decode("ascii"),
                    }
                }
            )
        contents.append({"role": role, "parts": parts or [{"text": ""}]})
    return "\n\n".join(p for p in system_parts if p), contents


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini rejects JSON-Schema keywords it does not implement."""
    drop = {"additionalProperties", "default", "$schema", "examples", "choices", "required_by"}
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in drop:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: _clean_schema(v) if isinstance(v, dict) else v
                        for k, v in value.items()}
        elif isinstance(value, dict):
            out[key] = _clean_schema(value)
        else:
            out[key] = value
    return out


__all__ = ["MODELS", "GeminiProvider"]
