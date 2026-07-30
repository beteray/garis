"""Other machines: GARIS Server and plain SSH hosts.

"Zainstaluj na serwerze nową usługę i skonfiguruj ją" is one goal with two agents.
The desktop plans, hands the goal to the server agent, and the server agent runs
its own plan through its own runtime with its own audit trail. What crosses the
wire is an outcome to achieve, not a list of commands — so the server can pick a
different method than the desktop imagined.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from typing import Any

from ..errors import ExecutionError
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, host_key
from ..store import dumps, loads


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "device_add",
        "Zapisuje urządzenie (serwer, inny komputer) w rejestrze GARIS-a.",
        params={
            "name": ParamSpec("string", "Nazwa urządzenia", required=True),
            "address": ParamSpec("string", "Adres, np. 10.0.0.5:8756", required=True),
            "role": ParamSpec("string", "Rola", default="server",
                              choices=("server", "desktop", "device")),
            "token_ref": ParamSpec("string", "Referencja do tokenu w sejfie", default=""),
        },
        effects=[Effect.WRITE],
        category="remote",
    )
    async def device_add(
        ctx: ToolContext, name: str, address: str, role: str = "server",
        token_ref: str = "",
    ) -> dict[str, Any]:
        device_id = uuid.uuid4().hex[:12]
        ctx.db.execute(
            "INSERT INTO devices(id, name, role, address, token_ref, facts, state,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            (device_id, name, role, address, token_ref or f"vault://device_{name}_token",
             "{}", "unknown", time.time()),
        )
        return {"id": device_id, "name": name, "address": address, "role": role}

    @registry.tool(
        "device_list",
        "Wypisuje znane urządzenia i ich stan.",
        params={},
        effects=[Effect.READ],
        category="remote",
    )
    async def device_list(ctx: ToolContext) -> list[dict[str, Any]]:
        rows = ctx.db.query("SELECT * FROM devices ORDER BY name")
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "role": r["role"],
                "address": r["address"],
                "state": r["state"],
                "seen_at": r["seen_at"],
                "facts": loads(r["facts"], {}),
            }
            for r in rows
        ]

    @registry.tool(
        "remote_goal",
        "Zleca cel agentowi GARIS na innym urządzeniu i zwraca identyfikator zadania.",
        params={
            "device": ParamSpec("string", "Nazwa albo id urządzenia", required=True),
            "goal": ParamSpec("string", "Co ma zostać osiągnięte", required=True),
            "wait": ParamSpec("bool", "Czekaj na zakończenie", default=False),
            "timeout": ParamSpec("int", "Limit oczekiwania w sekundach", default=900),
        },
        effects=[Effect.REMOTE, Effect.NETWORK],
        category="remote",
        timeout=3600.0,
        resources=lambda p: [host_key(str(p["device"]))],
        examples=("remote_goal(device='serwer', goal='zainstaluj i skonfiguruj nginx"
                  " z certyfikatem dla example.com')",),
    )
    async def remote_goal(
        ctx: ToolContext, device: str, goal: str, wait: bool = False, timeout: int = 900
    ) -> dict[str, Any]:
        target = _device(ctx, device)
        base = _base_url(target["address"])
        headers = _auth_headers(ctx, target)

        resp = await ctx.http.post(
            f"{base}/api/tasks",
            headers=headers,
            json_body={"goal": goal, "origin": "desktop"},
            timeout=60.0,
        )
        data = resp.raise_for_status().json()
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise ExecutionError(f"Serwer nie zwrócił identyfikatora zadania: {data}")
        ctx.progress(f"Zlecone na {target['name']}: {goal[:80]}")
        _mark_seen(ctx, target["id"], "online")

        if not wait:
            return {"device": target["name"], "task_id": task_id, "state": "running"}

        deadline = time.monotonic() + timeout
        delay = 2.0
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)   # back off; long jobs should not be polled hard
            status = await ctx.http.get(
                f"{base}/api/tasks/{task_id}", headers=headers, timeout=30.0
            )
            if not status.ok:
                continue
            payload = status.json()
            if payload.get("state") in ("finished", "failed", "stopped", "blocked"):
                return {
                    "device": target["name"],
                    "task_id": task_id,
                    "state": payload.get("state"),
                    "report": payload.get("report"),
                    "result": payload.get("result"),
                }
        raise ExecutionError(
            f"Zadanie na {target['name']} nie skończyło się w {timeout} s "
            f"(id {task_id}, nadal działa)"
        )

    @registry.tool(
        "remote_task_status",
        "Sprawdza stan zadania na innym urządzeniu.",
        params={
            "device": ParamSpec("string", "Urządzenie", required=True),
            "task_id": ParamSpec("string", "Identyfikator zadania", required=True),
        },
        effects=[Effect.NETWORK, Effect.READ],
        category="remote",
    )
    async def remote_task_status(
        ctx: ToolContext, device: str, task_id: str
    ) -> dict[str, Any]:
        target = _device(ctx, device)
        resp = await ctx.http.get(
            f"{_base_url(target['address'])}/api/tasks/{task_id}",
            headers=_auth_headers(ctx, target),
            timeout=30.0,
        )
        return resp.raise_for_status().json()

    @registry.tool(
        "ssh_run",
        "Uruchamia polecenie na serwerze przez SSH — dla hostów bez agenta GARIS.",
        params={
            "host": ParamSpec("string", "user@host", required=True),
            "command": ParamSpec("string", "Polecenie", required=True),
            "timeout": ParamSpec("int", "Limit czasu", default=120),
        },
        effects=[Effect.REMOTE, Effect.EXEC, Effect.NETWORK],
        category="remote",
        timeout=900.0,
        resources=lambda p: [host_key(str(p["host"]))],
    )
    async def ssh_run(
        ctx: ToolContext, host: str, command: str, timeout: int = 120
    ) -> dict[str, Any]:
        binary = shutil.which("ssh")
        if binary is None:
            raise ExecutionError("Brak klienta ssh na tym komputerze", retryable=False)
        process = await asyncio.create_subprocess_exec(
            binary,
            "-o", "BatchMode=yes",          # never hang a daemon on a passphrase prompt
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={min(timeout, 30)}",
            host, command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ExecutionError(f"ssh {host}: przekroczono {timeout} s") from None
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise ExecutionError(f"ssh {host} zwróciło {process.returncode}: {err[:400] or out[:400]}")
        return {"host": host, "exit_code": process.returncode,
                "stdout": out[:20000], "stderr": err[:4000]}


def _device(ctx: ToolContext, name: str) -> dict[str, Any]:
    row = ctx.db.one(
        "SELECT * FROM devices WHERE id = ? OR lower(name) = ?", (name, name.strip().lower())
    )
    if row is None:
        known = [r["name"] for r in ctx.db.query("SELECT name FROM devices")]
        raise ExecutionError(
            f"Nie znam urządzenia {name!r}. Znane: {', '.join(known) or 'brak'}",
            retryable=False,
        )
    return {
        "id": row["id"],
        "name": row["name"],
        "address": row["address"],
        "token_ref": row["token_ref"],
        "role": row["role"],
    }


def _base_url(address: str) -> str:
    if address.startswith(("http://", "https://")):
        return address.rstrip("/")
    return f"http://{address.rstrip('/')}"


def _auth_headers(ctx: ToolContext, device: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    ref = device.get("token_ref") or ""
    if ref and ctx.vault is not None:
        name = ref[len("vault://"):] if ref.startswith("vault://") else ref
        token = ctx.vault.get_optional(name)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _mark_seen(ctx: ToolContext, device_id: str, state: str) -> None:
    ctx.db.execute(
        "UPDATE devices SET state = ?, seen_at = ? WHERE id = ?",
        (state, time.time(), device_id),
    )


def _dump(value: Any) -> str:
    return dumps(value) if not isinstance(value, str) else json.dumps(value)


__all__ = ["register"]
