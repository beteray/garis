"""The API contract. If these break, every surface breaks with them."""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest

from garis.api import ApiServer
from garis.api.websocket import OP_TEXT, accept_key, encode_frame, read_frame
from garis.tasks import TaskState


@pytest.fixture
async def server(garis) -> AsyncIterator[ApiServer]:
    api = ApiServer(garis, host="127.0.0.1", port=0)
    await api.start()
    yield api
    await api.stop()


async def call(
    api: ApiServer,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = "use-real",
) -> tuple[int, Any]:
    reader, writer = await asyncio.open_connection(api.host, api.port)
    payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    lines = [f"{method} {path} HTTP/1.1", f"Host: {api.host}:{api.port}"]
    if token is not None:
        lines.append(f"Authorization: Bearer {api.token if token == 'use-real' else token}")
    if payload:
        lines += ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + payload)
    await writer.drain()

    raw = await reader.read(-1)
    writer.close()
    head, _, body_bytes = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, json.loads(body_bytes) if body_bytes else None


# --------------------------------------------------------------------- security


async def test_health_needs_no_token_but_reveals_nothing(server: ApiServer) -> None:
    status, body = await call(server, "GET", "/api/health", token=None)
    assert status == 200
    assert body == {"ok": True, "protocol": 1}


async def test_everything_else_requires_the_token(server: ApiServer) -> None:
    for method, path in [
        ("GET", "/api/state"),
        ("GET", "/api/tasks"),
        ("GET", "/api/memory"),
        ("GET", "/api/vault"),
        ("GET", "/api/config"),
        ("GET", "/api/audit"),
    ]:
        status, _ = await call(server, method, path, token=None)
        assert status == 401, f"{path} przepuściło żądanie bez tokenu"


async def test_a_wrong_token_is_rejected(server: ApiServer) -> None:
    status, _ = await call(server, "GET", "/api/state", token=secrets.token_urlsafe(32))
    assert status == 401


async def test_token_lives_in_the_vault_not_in_config(garis, server: ApiServer) -> None:
    """A token in a config file is a token in every backup."""
    assert garis.vault.get_optional("api_token") == server.token
    assert server.token not in json.dumps(garis.config.to_dict())


async def test_server_binds_to_loopback_only(server: ApiServer) -> None:
    assert server.host == "127.0.0.1"


async def test_unknown_path_is_a_clean_404(server: ApiServer) -> None:
    status, body = await call(server, "GET", "/api/nope")
    assert status == 404 and "error" in body


# ------------------------------------------------------------------------ state


async def test_state_gives_a_client_everything_at_once(server: ApiServer) -> None:
    status, body = await call(server, "GET", "/api/state")
    assert status == 200
    assert body["protocol"] == 1
    for key in ("identity", "persona", "voice", "counts", "models", "capabilities"):
        assert key in body, f"brak sekcji {key}"
    assert "payment" in body["capabilities"]["policy"]["confirm_effects"]


# ------------------------------------------------------------------------ tasks


async def test_submitting_a_goal_returns_an_id_both_ways(server: ApiServer) -> None:
    """`task_id` is what the server agent hand-off reads; `id` is plain REST."""
    status, body = await call(
        server, "POST", "/api/tasks", body={"goal": "sprawdź miejsce na dysku"}
    )
    assert status == 201
    assert body["task_id"] == body["id"]
    assert body["state"] in {s.value for s in TaskState}


async def test_a_goal_is_required(server: ApiServer) -> None:
    status, body = await call(server, "POST", "/api/tasks", body={"goal": "   "})
    assert status == 422 and "cel" in body["error"].lower()


async def test_task_detail_includes_steps_and_pending_approvals(
    server: ApiServer, garis
) -> None:
    _, created = await call(server, "POST", "/api/tasks", body={"goal": "sprawdź dysk"})
    await garis.tasks.wait(created["task_id"], timeout=20)

    status, body = await call(server, "GET", f"/api/tasks/{created['task_id']}")
    assert status == 200
    assert "steps" in body and "approvals" in body and "plan" in body


async def test_missing_task_is_404(server: ApiServer) -> None:
    status, _ = await call(server, "GET", "/api/tasks/nie-ma-takiego")
    assert status == 404


async def test_blocked_task_exposes_its_question(server: ApiServer, garis) -> None:
    """The UI must be able to show *why* a task stopped, not just that it did."""
    _, created = await call(
        server, "POST", "/api/tasks", body={"goal": "zainstaluj i skonfiguruj nginx"}
    )
    record = await garis.tasks.wait(created["task_id"], timeout=20)
    assert record.state is TaskState.BLOCKED

    status, body = await call(server, "GET", f"/api/tasks/{created['task_id']}")
    assert status == 200
    assert body["question"], "zablokowane zadanie nie pokazuje pytania"


async def test_stopping_a_task_over_http(server: ApiServer) -> None:
    _, created = await call(server, "POST", "/api/tasks", body={"goal": "cokolwiek"})
    status, body = await call(server, "POST", f"/api/tasks/{created['id']}/stop")
    assert status == 200 and "stopped" in body


# -------------------------------------------------------------------- approvals


async def test_approving_over_the_api_also_resumes_the_task(server: ApiServer, garis) -> None:
    """This is the mobile story: the yes arrives from elsewhere and work continues."""
    from garis.runtime import Effect, ParamSpec

    @garis.registry.tool(
        "api_pay",
        "Płaci za coś.",
        params={"amount": ParamSpec("float", required=True)},
        effects=[Effect.PAYMENT],
    )
    async def api_pay(ctx, amount):  # type: ignore[no-untyped-def]
        return {"paid": amount}

    from garis.errors import ApprovalRequired

    with pytest.raises(ApprovalRequired):
        await garis.runtime.perform_tool("api_pay", amount=15.0, task_id=None)

    status, body = await call(server, "GET", "/api/approvals")
    assert status == 200 and len(body["approvals"]) == 1
    approval_id = body["approvals"][0]["id"]

    status, body = await call(
        server, "POST", f"/api/approvals/{approval_id}", body={"approved": True, "by": "mobile"}
    )
    assert status == 200
    assert body["state"] == "approved" and body["resolved_by"] == "mobile"

    # The yes is now on file, so the same action goes through.
    result = await garis.runtime.perform_tool("api_pay", amount=15.0)
    assert result.ok


async def test_rejecting_is_recorded(server: ApiServer, garis) -> None:
    from garis.errors import ApprovalRequired
    from garis.runtime import Effect, ParamSpec

    @garis.registry.tool(
        "api_publish", "Publikuje.", params={"text": ParamSpec("string", required=True)},
        effects=[Effect.PUBLISH],
    )
    async def api_publish(ctx, text):  # type: ignore[no-untyped-def]
        return {"published": text}

    with pytest.raises(ApprovalRequired):
        await garis.runtime.perform_tool("api_publish", text="wpis")

    _, listing = await call(server, "GET", "/api/approvals")
    status, body = await call(
        server, "POST", f"/api/approvals/{listing['approvals'][0]['id']}",
        body={"approved": False},
    )
    assert status == 200 and body["state"] == "denied"


# ----------------------------------------------------------------------- memory


async def test_remember_list_correct_and_forget(server: ApiServer) -> None:
    status, created = await call(
        server, "POST", "/api/memory",
        body={"content": "Serwer produkcyjny to 10.0.0.5", "kind": "device",
              "subject": "serwer produkcyjny"},
    )
    assert status == 201

    status, listing = await call(server, "GET", "/api/memory?query=serwer")
    assert status == 200
    assert any("10.0.0.5" in m["content"] for m in listing["memories"])

    status, updated = await call(
        server, "PATCH", f"/api/memory/{created['id']}",
        body={"content": "Serwer produkcyjny to 10.0.0.9"},
    )
    assert status == 200 and "10.0.0.9" in updated["content"]

    status, body = await call(server, "DELETE", f"/api/memory/{created['id']}")
    assert status == 200 and body["forgotten"] is True


async def test_a_credential_sent_to_memory_is_refused_with_a_useful_message(
    server: ApiServer
) -> None:
    status, body = await call(
        server, "POST", "/api/memory",
        body={"content": "mój klucz to sk-abcdefghijklmnopqrstuvwx"},
    )
    assert status == 409
    assert "sejf" in body["error"].lower()


# ------------------------------------------------------------------------ vault


async def test_storing_a_secret_returns_only_a_reference(server: ApiServer) -> None:
    status, body = await call(
        server, "POST", "/api/vault",
        body={"name": "openai_api_key", "value": "sk-abcdefghijklmnopqrstuvwx",
              "note": "klucz do OpenAI"},
    )
    assert status == 201 and body["ref"] == "vault://openai_api_key"

    status, listing = await call(server, "GET", "/api/vault")
    assert status == 200
    names = [s["name"] for s in listing["secrets"]]
    assert "openai_api_key" in names
    assert "sk-" not in json.dumps(listing), "API nie może zwracać wartości sekretów"


# ------------------------------------------------------------------- inspection


async def test_tools_config_activity_and_audit(server: ApiServer) -> None:
    status, tools = await call(server, "GET", "/api/tools")
    assert status == 200 and len(tools["tools"]) > 40
    assert all({"name", "effects", "available"} <= set(t) for t in tools["tools"])

    status, config = await call(server, "GET", "/api/config")
    assert status == 200 and "autonomy" in config

    status, activity = await call(server, "GET", "/api/activity?minutes=5")
    assert status == 200 and "actions" in activity

    status, audit = await call(server, "GET", "/api/audit?limit=5")
    assert status == 200 and "audit" in audit


async def test_config_can_be_changed_and_persists(server: ApiServer, garis) -> None:
    status, body = await call(
        server, "PATCH", "/api/config", body={"dev.verbose": True, "persona.preset": "kolega"}
    )
    assert status == 200
    assert body["changed"]["dev.verbose"] is True
    assert garis.config.dev.verbose is True

    from garis.config import Config

    assert Config.load(garis.paths).persona.preset == "kolega"


# -------------------------------------------------------------------- websocket


async def ws_connect(api: ApiServer, *, token: str | None = None):  # type: ignore[no-untyped-def]
    reader, writer = await asyncio.open_connection(api.host, api.port)
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    used = api.token if token is None else token
    writer.write(
        (
            f"GET /ws?token={used} HTTP/1.1\r\n"
            f"Host: {api.host}:{api.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    return reader, writer, head.decode(), key


def mask(payload: bytes) -> bytes:
    """Client frames must be masked; the server rejects unmasked ones."""
    key = secrets.token_bytes(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    header = bytearray([0x80 | OP_TEXT, 0x80 | len(payload)])
    return bytes(header) + key + masked


async def test_handshake_is_correct_and_authenticated(server: ApiServer) -> None:
    _reader, writer, head, key = await ws_connect(server)
    assert "101 Switching Protocols" in head
    assert f"Sec-WebSocket-Accept: {accept_key(key)}" in head
    writer.close()


async def test_websocket_without_a_token_is_refused(server: ApiServer) -> None:
    _reader, writer, head, _ = await ws_connect(server, token="zly-token")
    assert "401" in head
    writer.close()


async def test_state_arrives_before_any_event(server: ApiServer) -> None:
    reader, writer, _, _ = await ws_connect(server)
    frame = await asyncio.wait_for(read_frame(reader, expect_mask=False), timeout=5)
    assert frame is not None
    message = json.loads(frame.text)
    assert message["topic"] == "state"
    assert message["data"]["protocol"] == 1
    writer.close()


async def test_task_lifecycle_is_streamed(server: ApiServer, garis) -> None:
    reader, writer, _, _ = await ws_connect(server)
    await asyncio.wait_for(read_frame(reader, expect_mask=False), timeout=5)  # state

    await call(server, "POST", "/api/tasks",
               body={"goal": "sprawdź miejsce na dysku"})

    topics: list[str] = []
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        frame = await asyncio.wait_for(read_frame(reader, expect_mask=False), timeout=20)
        if frame is None:
            break
        topics.append(json.loads(frame.text)["topic"])
        if any(t in topics for t in ("task.finished", "task.failed", "task.blocked")):
            break

    assert "task.created" in topics
    assert "agent.state" in topics, "kula w UI nie miałaby czym animować stanów"
    writer.close()


async def test_server_survives_a_client_that_sends_rubbish(server: ApiServer) -> None:
    reader, writer, _, _ = await ws_connect(server)
    await asyncio.wait_for(read_frame(reader, expect_mask=False), timeout=5)
    writer.write(encode_frame(b"nie zamaskowane", OP_TEXT))       # unmasked: illegal
    await writer.drain()
    writer.close()

    # The API must still answer after hanging up on a bad client.
    status, _ = await call(server, "GET", "/api/health", token=None)
    assert status == 200


async def test_client_text_is_accepted_and_ignored(server: ApiServer) -> None:
    reader, writer, _, _ = await ws_connect(server)
    await asyncio.wait_for(read_frame(reader, expect_mask=False), timeout=5)
    writer.write(mask(b'{"hello": true}'))
    await writer.drain()

    status, _ = await call(server, "GET", "/api/health", token=None)
    assert status == 200
    assert server.client_count == 1
    writer.close()


async def test_stopping_the_server_closes_clients(server: ApiServer) -> None:
    reader, _writer, _, _ = await ws_connect(server)
    await asyncio.wait_for(read_frame(reader, expect_mask=False), timeout=5)
    assert server.client_count == 1
    await server.stop()
    assert server.client_count == 0
