"""Local HTTP + WebSocket server: the only way in from outside the process.

Bound to loopback and token-authenticated. The desktop UI, the mobile remote (via
a relay) and the desktop→server hand-off all speak this one protocol, which is why
it exists before any interface: without it the GUI would reach into the engine's
internals and the layering would rot in a week.

Requests change state through the same objects the CLI uses — ``tasks.submit``,
``approvals.resolve``, ``memory.remember`` — so there is no second, weaker path
into the runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..errors import GarisError, SecretNotAllowed
from ..events import Topic
from ..memory import MemoryKind
from ..tasks import TaskState
from . import protocol as proto
from .protocol import Reply, error, ok
from .websocket import (
    OP_CLOSE,
    OP_PING,
    Connection,
    ProtocolError,
    handshake_response,
    read_frame,
)

MAX_BODY = 1 << 20
MAX_HEADER_LINES = 100
TOKEN_SECRET_NAME = "api_token"

# The desktop window is not same-origin with the engine and never can be.
#
# A packaged Tauri app serves its own pages from a custom scheme — tauri://localhost
# on macOS and Linux, http://tauri.localhost on Windows — while the engine listens
# on 127.0.0.1. Every fetch the window makes is therefore cross-origin, and without
# these headers the browser engine refuses all of them before the request is even
# authenticated. That failure looks exactly like a broken app: the window opens,
# says it is connecting, and nothing it offers ever works.
#
# Listed rather than mirrored with "*": the API is loopback-only and carries a
# bearer token, and a wildcard would let any page the user happens to have open
# talk to their agent.
ALLOWED_ORIGINS = frozenset(
    {
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    }
)
# Vite during development, on whatever port it picked.
LOCAL_ORIGIN = re.compile(r"^http://(?:localhost|127\.0\.0\.1)(?::\d+)?$")


def _origin_allowed(origin: str) -> bool:
    return bool(origin) and (origin in ALLOWED_ORIGINS or bool(LOCAL_ORIGIN.fullmatch(origin)))


def _cors_headers(origin: str) -> dict[str, str]:
    """Headers that let the window talk to the engine, and nobody else."""
    if not _origin_allowed(origin):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Max-Age": "600",
        # Caches must not hand one origin's response to another.
        "Vary": "Origin",
    }


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    params: dict[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        import json

        if not self.body:
            return {}
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @property
    def token(self) -> str:
        header = self.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        # Browsers cannot set headers on a WebSocket handshake, so the token may
        # ride in the query string for /ws only.
        return self.query.get("token", "")


Handler = Callable[[Request], Awaitable[Reply]]


class ApiServer:
    def __init__(
        self,
        garis: Any,
        *,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
    ) -> None:
        self.garis = garis
        self.host = host or garis.config.api.host
        self.port = port if port is not None else garis.config.api.port
        self.token = token or ensure_token(garis)
        self._server: asyncio.Server | None = None
        self._clients: set[Connection] = set()
        self._routes = self._build_routes()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, self.host, self.port)
        # Port 0 means "pick one" — read back what we actually got.
        socket_info = self._server.sockets[0].getsockname()
        self.port = socket_info[1]

    async def stop(self) -> None:
        for connection in list(self._clients):
            await connection.close(1001, "serwer się zatrzymuje")
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
            self._server = None

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def __aenter__(self) -> ApiServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------ plumbing

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await self._read_request(reader)
            if request is None:
                writer.close()
                return

            if request.path == "/ws":
                await self._upgrade(request, reader, writer)
                return

            origin = request.headers.get("origin", "")

            # The preflight has to be answered before the real request is ever
            # sent, and it carries no credentials — so it is handled ahead of
            # routing and authentication rather than inside them.
            if request.method == "OPTIONS":
                headers = _cors_headers(origin)
                # 200 rather than 204: _write_reply always sends a body, and a
                # 204 that carries one is malformed.
                await self._write_reply(writer, Reply(200 if headers else 403, {}, headers))
                return

            reply = await self._dispatch(request)
            reply.headers.update(_cors_headers(origin))
            await self._write_reply(writer, reply)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._write_reply(writer, error(500, "Błąd serwera", detail=str(exc)))
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _read_request(self, reader: asyncio.StreamReader) -> Request | None:
        line = await reader.readline()
        if not line:
            return None
        try:
            method, target, _ = line.decode("latin-1").split(" ", 2)
        except ValueError:
            return None

        headers: dict[str, str] = {}
        for _ in range(MAX_HEADER_LINES):
            raw = await reader.readline()
            if raw in (b"\r\n", b"\n", b""):
                break
            name, _, value = raw.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()

        length = min(int(headers.get("content-length", "0") or 0), MAX_BODY)
        body = await reader.readexactly(length) if length else b""

        parsed = urllib.parse.urlsplit(target)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        return Request(method.upper(), parsed.path.rstrip("/") or "/", query, headers, body)

    async def _write_reply(self, writer: asyncio.StreamWriter, reply: Reply) -> None:
        payload = reply.encode()
        head = [
            f"HTTP/1.1 {reply.status} {_reason(reply.status)}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(payload)}",
            "Connection: close",
            # A local agent's API is not a web page; nothing here should ever be
            # embedded, cached or sniffed.
            "Cache-Control: no-store",
            "X-Content-Type-Options: nosniff",
        ]
        head += [f"{k}: {v}" for k, v in reply.headers.items()]
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + payload)
        await writer.drain()

    async def _dispatch(self, request: Request) -> Reply:
        for method, pattern, handler, needs_auth in self._routes:
            if method != request.method:
                continue
            match = pattern.fullmatch(request.path)
            if match is None:
                continue
            if needs_auth and not self._authorised(request):
                return error(401, "Brak autoryzacji")
            request.params = match.groupdict()
            try:
                return await handler(request)
            except GarisError as exc:
                return error(400, exc.explain(), detail=str(exc))
            except KeyError as exc:
                return error(404, f"Nie ma takiego elementu: {exc}")
        return error(404, f"Nieznana ścieżka {request.path}")

    def _authorised(self, request: Request) -> bool:
        return secrets.compare_digest(request.token, self.token)

    # ----------------------------------------------------------------- websocket

    async def _upgrade(
        self, request: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        key = request.headers.get("sec-websocket-key", "")
        if not key or not self._authorised(request):
            await self._write_reply(writer, error(401, "Brak autoryzacji"))
            return

        writer.write(handshake_response(key))
        await writer.drain()

        connection = Connection(reader, writer)
        self._clients.add(connection)
        subscription = self.garis.bus.subscribe(*proto.STREAMED_TOPICS)

        async def pump() -> None:
            async for event in subscription:
                await connection.send_text(
                    proto.envelope(event.topic, event.payload, event.at)
                )
                if connection.closed:
                    break

        pumping = asyncio.ensure_future(pump())
        try:
            # Send the current state immediately: a client should never have to
            # wait for the next event to know what is going on.
            await connection.send_text(
                proto.envelope("state", proto.state_view(self.garis), _now())
            )
            while True:
                frame = await read_frame(reader)
                if frame is None or frame.opcode == OP_CLOSE:
                    break
                if frame.opcode == OP_PING:
                    await connection.send_pong(frame.payload)
        except (ConnectionError, asyncio.IncompleteReadError, ProtocolError):
            pass
        finally:
            pumping.cancel()
            subscription.close()
            self._clients.discard(connection)
            await connection.close()

    # -------------------------------------------------------------------- routes

    def _build_routes(self) -> list[tuple[str, re.Pattern[str], Handler, bool]]:
        def route(
            method: str, path: str, handler: Handler, *, auth: bool = True
        ) -> tuple[str, re.Pattern[str], Handler, bool]:
            pattern = re.compile(
                re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path.rstrip("/") or "/")
            )
            return (method, pattern, handler, auth)

        return [
            route("GET", "/api/health", self._health, auth=False),
            route("GET", "/api/state", self._state),

            route("GET", "/api/tasks", self._tasks),
            route("POST", "/api/tasks", self._create_task),
            route("GET", "/api/tasks/{id}", self._task),
            route("POST", "/api/tasks/{id}/stop", self._stop_task),
            route("POST", "/api/tasks/{id}/resume", self._resume_task),

            route("GET", "/api/approvals", self._approvals),
            route("POST", "/api/approvals/{id}", self._resolve_approval),

            route("GET", "/api/memory", self._memory),
            route("POST", "/api/memory", self._remember),
            route("PATCH", "/api/memory/{id}", self._correct_memory),
            route("DELETE", "/api/memory/{id}", self._forget),

            route("GET", "/api/vault", self._vault),
            route("POST", "/api/vault", self._store_secret),
            route("DELETE", "/api/vault/{name}", self._delete_secret),

            route("GET", "/api/providers", self._providers),
            route("POST", "/api/providers/check", self._check_providers),

            route("GET", "/api/devices", self._devices),
            route("GET", "/api/tools", self._tools),
            route("GET", "/api/activity", self._activity),
            route("GET", "/api/audit", self._audit),

            route("GET", "/api/config", self._config),
            route("PATCH", "/api/config", self._patch_config),
        ]

    # ------------------------------------------------------------------ handlers

    async def _health(self, request: Request) -> Reply:
        """Unauthenticated on purpose: a UI needs to know the engine is up.

        Reveals only liveness and the protocol version — nothing about the user.
        """
        return ok({"ok": True, "protocol": proto.PROTOCOL_VERSION})

    async def _state(self, request: Request) -> Reply:
        return ok(proto.state_view(self.garis))

    # --- tasks ---

    async def _tasks(self, request: Request) -> Reply:
        state = request.query.get("state")
        records = self.garis.tasks.store.list(
            state=TaskState(state) if state else None,
            active_only=request.query.get("active") == "true",
            limit=int(request.query.get("limit", "50")),
        )
        return ok({"tasks": [proto.task_view(r) for r in records]})

    async def _create_task(self, request: Request) -> Reply:
        payload = request.json()
        goal = str(payload.get("goal", "")).strip()
        if not goal:
            return error(422, "Podaj cel zadania")
        criteria = tuple(str(c) for c in payload.get("criteria", []) if str(c).strip())
        record = self.garis.tasks.submit(
            goal,
            criteria=criteria,
            origin=str(payload.get("origin", "user")),
            target=str(payload.get("target", "local")),
        )
        # `task_id` is what tools/remote.py reads back when the desktop hands a
        # goal to the server agent; `id` is here for plain REST expectations.
        return ok({"task_id": record.id, "id": record.id, **proto.task_view(record)},
                  status=201)

    async def _task(self, request: Request) -> Reply:
        record = self.garis.tasks.store.get(request.params["id"])
        if record is None:
            return error(404, "Nie ma takiego zadania")
        view = proto.task_view(record, full=True)
        view["steps"] = [proto.step_view(s) for s in self.garis.tasks.steps(record.id)]
        view["approvals"] = [
            proto.approval_view(a)
            for a in self.garis.runtime.approvals.pending(task_id=record.id)
        ]
        return ok(view)

    async def _stop_task(self, request: Request) -> Reply:
        stopped = self.garis.tasks.stop(request.params["id"])
        return ok({"stopped": stopped})

    async def _resume_task(self, request: Request) -> Reply:
        record = self.garis.tasks.resume(request.params["id"])
        return ok(proto.task_view(record))

    # --- approvals ---

    async def _approvals(self, request: Request) -> Reply:
        pending = self.garis.runtime.approvals.pending()
        return ok({"approvals": [proto.approval_view(a) for a in pending]})

    async def _resolve_approval(self, request: Request) -> Reply:
        payload = request.json()
        approved = bool(payload.get("approved", False))
        by = str(payload.get("by", "ui"))
        request_record = self.garis.runtime.approvals.resolve(
            request.params["id"], approved, by=by
        )
        # Approving is only half of it: the task has to actually continue.
        if request_record.task_id:
            self.garis.tasks.resume(request_record.task_id)
        return ok(proto.approval_view(request_record))

    # --- memory ---

    async def _memory(self, request: Request) -> Reply:
        query = request.query.get("query", "").strip()
        limit = int(request.query.get("limit", "100"))
        kind = request.query.get("kind")
        if query:
            records = self.garis.memory.search(query, limit=limit)
        else:
            records = self.garis.memory.list(kind=kind, limit=limit)
        return ok({
            "memories": [proto.memory_view(r) for r in records],
            "stats": self.garis.memory.stats(),
        })

    async def _remember(self, request: Request) -> Reply:
        payload = request.json()
        content = str(payload.get("content", "")).strip()
        if not content:
            return error(422, "Podaj treść do zapamiętania")
        try:
            record = self.garis.memory.remember(
                content,
                kind=payload.get("kind", MemoryKind.FACT),
                subject=str(payload.get("subject", "")),
                tags=tuple(str(t) for t in payload.get("tags", [])),
                scope=payload.get("scope", "permanent"),
            )
        except SecretNotAllowed as exc:
            # Not an error the user did wrong — a redirect to the right place.
            return error(409, exc.explain(), detail=str(exc))
        return ok(proto.memory_view(record), status=201)

    async def _correct_memory(self, request: Request) -> Reply:
        payload = request.json()
        record = self.garis.memory.update(
            request.params["id"],
            content=payload.get("content"),
            subject=payload.get("subject"),
            tags=payload.get("tags"),
            pinned=payload.get("pinned"),
        )
        return ok(proto.memory_view(record))

    async def _forget(self, request: Request) -> Reply:
        return ok({"forgotten": self.garis.memory.forget(request.params["id"])})

    # --- vault ---

    async def _vault(self, request: Request) -> Reply:
        return ok({"secrets": [info.to_dict() for info in self.garis.vault.list()]})

    async def _store_secret(self, request: Request) -> Reply:
        payload = request.json()
        name = str(payload.get("name", "")).strip()
        value = str(payload.get("value", ""))
        if not name or not value:
            return error(422, "Podaj nazwę i wartość")
        ref = self.garis.memory.remember_secret(
            name, value, note=str(payload.get("note", ""))
        )
        # Awaited, not fired off: the reply carries the provider's real state, so
        # a settings screen can say "key rejected" instead of "saved" and let the
        # user discover the truth during their first task.
        await self._reload_providers("vault")
        return ok(
            {"ref": ref, "name": name, "providers": self._provider_views()},
            status=201,
        )

    async def _delete_secret(self, request: Request) -> Reply:
        deleted = self.garis.vault.delete(request.params["name"])
        if deleted:
            await self._reload_providers("vault")
        return ok({"deleted": deleted})

    # --- providers ---

    async def _providers(self, request: Request) -> Reply:
        return ok({"providers": self._provider_views()})

    async def _check_providers(self, request: Request) -> Reply:
        """Force a health check. This is what a "Sprawdź ponownie" button calls."""
        pool = getattr(self.garis, "providers", None)
        if pool is not None:
            await pool.refresh(force=True)
        return ok({"providers": self._provider_views()})

    def _provider_views(self) -> list[dict[str, Any]]:
        router = self.garis.router
        return [router.describe_provider(p) for p in router.providers]

    async def _reload_providers(self, reason: str) -> None:
        reload = getattr(self.garis, "reload_providers", None)
        if reload is not None:
            await reload(reason=reason)

    # --- inspection ---

    async def _devices(self, request: Request) -> Reply:
        rows = self.garis.db.query("SELECT * FROM devices ORDER BY name")
        return ok({"devices": [dict(row) for row in rows]})

    async def _tools(self, request: Request) -> Reply:
        return ok({"tools": [proto.tool_view(s) for s in self.garis.registry.all()]})

    async def _activity(self, request: Request) -> Reply:
        minutes = int(request.query.get("minutes", "60"))
        return ok(self.garis.runtime.audit.activity_since(_now() - minutes * 60))

    async def _audit(self, request: Request) -> Reply:
        task_id = request.query.get("task")
        limit = int(request.query.get("limit", "100"))
        entries = (
            self.garis.runtime.audit.for_task(task_id, limit)
            if task_id
            else self.garis.runtime.audit.recent(limit)
        )
        return ok({"audit": [entry.to_dict() for entry in entries]})

    # --- config ---

    async def _config(self, request: Request) -> Reply:
        return ok(self.garis.config.to_dict())

    async def _patch_config(self, request: Request) -> Reply:
        payload = request.json()
        # Through the settings service rather than straight onto the config: it
        # validates on a copy (so a bad third field cannot half-apply), saves,
        # and announces the paths that moved so the rest of the engine follows.
        paths = self.garis.settings.apply(payload)
        if any(path.startswith("models") for path in paths):
            await self._reload_providers("config")
        changed = {str(key): self.garis.config.get(str(key)) for key in payload}
        self.garis.bus.emit(Topic.NOTICE, message="Ustawienia zapisane", importance=1,
                            silent=True)
        return ok({"changed": changed, "paths": list(paths)})


def ensure_token(garis: Any) -> str:
    """Read the API token from the vault, minting one on first run.

    Generated rather than configured: a token in a config file is a token in a
    backup. It lives in the vault like any other credential.
    """
    ref = garis.config.api.token_ref or f"vault://{TOKEN_SECRET_NAME}"
    name = ref[len("vault://"):] if ref.startswith("vault://") else ref
    existing = garis.vault.get_optional(name)
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    garis.vault.set(name, token, kind="token", note="Token lokalnego API GARIS-a")
    return token


def _now() -> float:
    import time

    return time.time()


_REASONS = {
    200: "OK", 201: "Created", 400: "Bad Request", 401: "Unauthorized",
    404: "Not Found", 409: "Conflict", 422: "Unprocessable Entity",
    500: "Internal Server Error",
}


def _reason(status: int) -> str:
    return _REASONS.get(status, "OK")


__all__ = ["ApiServer", "Request", "ensure_token"]
