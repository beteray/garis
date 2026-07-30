"""Local models via Ollama.

Stage 1 ships cloud-only by decision, so this adapter exists as a fully wired
slot: nothing above it changes when local models are turned on. It is only ever
selected if a probe found a running Ollama, which is why ``available()`` reports a
cached probe result instead of guessing from configuration.
"""

from __future__ import annotations

import json
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

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

# Local models cost nothing per token; the router still needs quality and speed
# so it can send private or trivial work here and hard reasoning to the cloud.
KNOWN_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        name="qwen2.5:14b",
        provider="ollama",
        jobs=frozenset({Job.CHAT, Job.CLASSIFY, Job.SUMMARIZE, Job.EXTRACT, Job.PLAN}),
        capabilities=frozenset({Capability.LOCAL, Capability.TOOLS, Capability.STREAMING,
                                Capability.JSON_MODE}),
        quality=0.62, speed=0.5, context_tokens=32_000, local=True,
    ),
    ModelSpec(
        name="llama3.2:3b",
        provider="ollama",
        jobs=frozenset({Job.CHAT, Job.CLASSIFY, Job.SUMMARIZE}),
        capabilities=frozenset({Capability.LOCAL, Capability.STREAMING}),
        quality=0.42, speed=0.85, context_tokens=16_000, local=True,
    ),
)


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        http: HttpClient | None = None,
        base_url: str = "",
        models: Sequence[ModelSpec] = KNOWN_MODELS,
    ) -> None:
        self._http = http or HttpClient()
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._catalog = tuple(models)
        self._installed: tuple[str, ...] = ()
        self._probed = False

    def models(self) -> Sequence[ModelSpec]:
        """Only models actually pulled on this machine are offered to the router."""
        if not self._probed:
            return ()
        return tuple(m for m in self._catalog if m.name in self._installed)

    def available(self) -> bool:
        return self._probed and bool(self._installed)

    async def probe(self, *, timeout: float = 2.0) -> bool:
        """Ask the local daemon what it has. Cheap, and failure is not an error."""
        try:
            resp = await self._http.get(f"{self._base}/api/tags", timeout=timeout)
            if not resp.ok:
                return False
            payload = resp.json()
        except Exception:
            self._probed = True
            self._installed = ()
            return False
        self._installed = tuple(
            entry.get("name", "") for entry in payload.get("models", []) if entry.get("name")
        )
        self._probed = True
        return bool(self._installed)

    async def complete(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
        timeout: float = 300.0,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model.name,
            "messages": [_encode(m) for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            body["format"] = "json"
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]

        resp = await self._http.post(f"{self._base}/api/chat", json_body=body, timeout=timeout)
        data = resp.raise_for_status().json()
        message = data.get("message") or {}
        calls = tuple(
            ToolCall(
                id=f"local-{index}",
                name=(call.get("function") or {}).get("name", ""),
                arguments=_as_dict((call.get("function") or {}).get("arguments")),
            )
            for index, call in enumerate(message.get("tool_calls") or [])
        )
        usage = Usage(
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            cost=0.0,
        )
        return Completion(
            text=message.get("content", ""),
            model=model.name,
            provider=self.name,
            tool_calls=calls,
            usage=usage,
            finish_reason=data.get("done_reason", "stop"),
            raw=data,
        )

    async def stream(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 300.0,
    ) -> AsyncIterator[str]:
        # Ollama streams newline-delimited JSON rather than SSE.
        body = {
            "model": model.name,
            "messages": [_encode(m) for m in messages],
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        resp = await self._http.post(f"{self._base}/api/chat", json_body=body, timeout=timeout)
        if not resp.ok:
            raise ProviderError(f"Ollama: HTTP {resp.status}")
        for line in resp.text.splitlines():
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            piece = (chunk.get("message") or {}).get("content")
            if piece:
                yield piece


def _encode(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {"role": "tool", "content": message.content}
    return {"role": message.role.value, "content": message.content}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = ["KNOWN_MODELS", "OllamaProvider"]
