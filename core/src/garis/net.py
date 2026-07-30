"""HTTP transport for model providers, feeds and web tools.

Deliberately built on ``urllib`` in a worker thread rather than a third-party
client: it honours system proxy settings and the OS trust store out of the box,
adds no dependency to a process that must start with Windows, and gives tests a
single seam (``Transport``) to fake instead of patching a socket layer.
"""

from __future__ import annotations

import gzip
import json
import ssl
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import ExecutionError

USER_AGENT = "GARIS/0.1 (+https://github.com/beteray/garis)"
DEFAULT_TIMEOUT = 60.0


@dataclass(slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ExecutionError(f"Odpowiedź nie jest JSON-em: {self.text[:200]}") from exc

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def raise_for_status(self) -> Response:
        if not self.ok:
            raise ExecutionError(
                f"HTTP {self.status} z {self.url}: {self.text[:300]}",
                # 408/429/5xx are worth another attempt; 4xx generally is not.
                retryable=self.status in (408, 425, 429) or self.status >= 500,
            )
        return self


@dataclass(slots=True)
class Request:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout: float = DEFAULT_TIMEOUT


class Transport:
    """Blocking request execution. Swap this out in tests."""

    def __init__(self, *, verify: bool = True) -> None:
        self._ctx = ssl.create_default_context()
        if not verify:  # pragma: no cover - opt-in escape hatch for self-signed hosts
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def send(self, request: Request) -> Response:
        req = urllib.request.Request(
            request.url, data=request.body, method=request.method.upper()
        )
        for key, value in request.headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=request.timeout, context=self._ctx) as resp:
                raw = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if headers.get("content-encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return Response(resp.status, headers, raw, request.url)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            return Response(exc.code, headers, raw, request.url)
        except urllib.error.URLError as exc:
            raise ExecutionError(f"Brak połączenia z {request.url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ExecutionError(f"Przekroczono czas oczekiwania: {request.url}") from exc

    def stream(self, request: Request) -> Iterator[bytes]:
        """Yield response lines as they arrive — used for SSE token streaming."""
        req = urllib.request.Request(
            request.url, data=request.body, method=request.method.upper()
        )
        for key, value in request.headers.items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=request.timeout, context=self._ctx) as resp:
                if resp.status >= 400:
                    raise ExecutionError(f"HTTP {resp.status} z {request.url}")
                yield from resp
        except urllib.error.HTTPError as exc:
            raise ExecutionError(
                f"HTTP {exc.code} z {request.url}: {exc.read()[:300]!r}",
                retryable=exc.code in (408, 425, 429) or exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            raise ExecutionError(f"Brak połączenia z {request.url}: {exc.reason}") from exc


class HttpClient:
    """Async facade over a blocking transport."""

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport or Transport()

    def _prepare(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None,
        json_body: Any = None,
        data: bytes | None = None,
        timeout: float,
    ) -> Request:
        merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        merged.update(headers or {})
        body = data
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        return Request(method, url, merged, body, timeout)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        data: bytes | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Response:
        import asyncio

        req = self._prepare(
            method, url, headers=headers, json_body=json_body, data=data, timeout=timeout
        )
        return await asyncio.to_thread(self._transport.send, req)

    async def get(self, url: str, **kw: Any) -> Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> Response:
        return await self.request("POST", url, **kw)

    async def stream_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterate ``data:`` payloads of a server-sent-event stream."""
        import asyncio

        req = self._prepare(
            "POST", url, headers=headers, json_body=json_body, data=None, timeout=timeout
        )
        queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()

        def pump() -> None:
            try:
                for line in self._transport.stream(req):
                    asyncio.run_coroutine_threadsafe(queue.put(line), loop).result()
            except BaseException as exc:
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        task = asyncio.create_task(asyncio.to_thread(pump))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                line = item.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue
        finally:
            task.cancel()


class FakeTransport(Transport):
    """Scripted transport for tests: map a URL substring to a response."""

    def __init__(self, handler: Callable[[Request], Response]) -> None:
        self.handler = handler
        self.calls: list[Request] = []

    def send(self, request: Request) -> Response:
        self.calls.append(request)
        return self.handler(request)

    def stream(self, request: Request) -> Iterator[bytes]:
        self.calls.append(request)
        yield from self.handler(request).body.splitlines(keepends=True)


__all__ = [
    "DEFAULT_TIMEOUT",
    "USER_AGENT",
    "FakeTransport",
    "HttpClient",
    "Request",
    "Response",
    "Transport",
]
