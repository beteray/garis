"""Terminal access: PowerShell on Windows, the system shell elsewhere.

The escape hatch that makes "GARIS can do anything on this machine" real. It is
also the most abused capability in agent systems, so three things are fixed here:
output is captured and truncated rather than streamed into a prompt, a timeout is
always in force, and elevation is a declared, separately gated tool instead of a
flag on the ordinary one.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from typing import Any

from ..errors import ExecutionError, Unsupported
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, app_key

MAX_OUTPUT_CHARS = 20_000


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "shell_run",
        "Uruchamia polecenie w powłoce systemowej (PowerShell na Windows) i zwraca wynik.",
        params={
            "command": ParamSpec("string", "Polecenie do wykonania", required=True),
            "cwd": ParamSpec("path", "Katalog roboczy", default=""),
            "timeout": ParamSpec("int", "Limit czasu w sekundach", default=120),
            "shell": ParamSpec(
                "string",
                "Wymuszona powłoka",
                default="auto",
                choices=("auto", "powershell", "cmd", "bash", "sh"),
            ),
        },
        effects=[Effect.EXEC],
        category="shell",
        timeout=900.0,
        resources=lambda p: [app_key("shell")],
        examples=(
            "shell_run(command='Get-Service -Name Spooler')",
            "shell_run(command='git status', cwd='C:/projekt')",
        ),
    )
    async def shell_run(
        ctx: ToolContext,
        command: str,
        cwd: str = "",
        timeout: int = 120,
        shell: str = "auto",
    ) -> dict[str, Any]:
        return await _run(ctx, command, cwd, timeout, shell, elevated=False)

    @registry.tool(
        "shell_run_elevated",
        "Uruchamia polecenie z uprawnieniami administratora.",
        params={
            "command": ParamSpec("string", "Polecenie do wykonania", required=True),
            "cwd": ParamSpec("path", "Katalog roboczy", default=""),
            "timeout": ParamSpec("int", "Limit czasu w sekundach", default=300),
            "reason": ParamSpec("string", "Dlaczego potrzebne są uprawnienia", default=""),
        },
        effects=[Effect.EXEC, Effect.ELEVATE, Effect.SYSTEM_CONFIG],
        category="shell",
        timeout=900.0,
        resources=lambda p: [app_key("shell"), app_key("elevated")],
        danger_note="Polecenie wykona się z pełnymi uprawnieniami systemu.",
    )
    async def shell_run_elevated(
        ctx: ToolContext,
        command: str,
        cwd: str = "",
        timeout: int = 300,
        reason: str = "",
    ) -> dict[str, Any]:
        del reason  # captured in the audit row via the action intent
        return await _run(ctx, command, cwd, timeout, "auto", elevated=True)

    @registry.tool(
        "app_launch",
        "Uruchamia program albo otwiera plik w domyślnej aplikacji.",
        params={
            "target": ParamSpec("string", "Program, ścieżka albo URL", required=True),
            "arguments": ParamSpec("list", "Argumenty wywołania", default=[]),
            "wait": ParamSpec("bool", "Czekaj na zakończenie", default=False),
        },
        effects=[Effect.EXEC],
        category="shell",
        resources=lambda p: [app_key(str(p.get("target", "app")))],
    )
    async def app_launch(
        ctx: ToolContext,
        target: str,
        arguments: list[str] | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        args = [str(a) for a in (arguments or [])]
        if sys.platform == "win32":
            resolved = shutil.which(target) or target
            argv = [resolved, *args]
        elif sys.platform == "darwin":  # pragma: no cover - not a target platform yet
            argv = ["open", target, *args]
        else:
            resolved = shutil.which(target)
            argv = [resolved, *args] if resolved else ["xdg-open", target]

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if not wait:
            return {"target": target, "pid": process.pid, "waited": False}
        code = await process.wait()
        return {"target": target, "pid": process.pid, "exit_code": code, "waited": True}


async def _run(
    ctx: ToolContext,
    command: str,
    cwd: str,
    timeout: int,
    shell: str,
    *,
    elevated: bool,
) -> dict[str, Any]:
    argv = _build_argv(command, shell, elevated=elevated)
    workdir = os.path.expandvars(os.path.expanduser(cwd)) if cwd else None
    if workdir and not os.path.isdir(workdir):
        raise ExecutionError(f"Katalog roboczy nie istnieje: {workdir}", retryable=False)

    ctx.progress(f"Uruchamiam: {command[:120]}")
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ExecutionError(
            f"Polecenie przekroczyło {timeout} s i zostało zatrzymane: {command[:120]}"
        ) from None

    out = _decode(stdout)
    err = _decode(stderr)
    result = {
        "command": command,
        "exit_code": process.returncode,
        "stdout": _truncate(out),
        "stderr": _truncate(err),
        "elevated": elevated,
    }
    if process.returncode != 0:
        # A non-zero exit is data, not a crash: the agent decides whether the goal
        # failed. Only surface it as an error when there is nothing else to read.
        detail = (err or out).strip()[:400]
        raise ExecutionError(
            f"Polecenie zakończyło się kodem {process.returncode}: {detail}",
            retryable=True,
        )
    return result


def _build_argv(command: str, shell: str, *, elevated: bool) -> list[str]:
    if shell == "auto":
        shell = "powershell" if sys.platform == "win32" else "bash"

    if shell in ("powershell", "cmd") and sys.platform != "win32":
        raise Unsupported(f"Powłoka {shell} jest dostępna tylko na Windows")

    if shell == "powershell":
        if elevated:
            # Start-Process -Verb RunAs triggers the UAC prompt Windows requires;
            # GARIS cannot and should not bypass that dialog.
            inner = command.replace('"', '`"')
            wrapped = (
                "Start-Process -FilePath powershell -Verb RunAs -Wait "
                f'-ArgumentList \'-NoProfile\',\'-Command\',"{inner}"'
            )
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped]
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]

    if shell == "cmd":
        return ["cmd", "/c", command]

    if elevated:
        sudo = shutil.which("sudo")
        if sudo is None:
            raise Unsupported("Brak sudo — nie mogę podnieść uprawnień")
        # -n: never prompt. A password prompt in a background daemon would hang
        # forever; failing fast lets the agent report a real blocker instead.
        return [sudo, "-n", shell, "-lc", command]

    resolved = shutil.which(shell)
    if resolved is None:
        raise Unsupported(f"Brak powłoki {shell}")
    return [resolved, "-lc", command]


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1250", "cp852"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n…[obcięto {len(text) - limit} znaków]…\n{text[-half:]}"


__all__ = ["register"]
