"""Installing and updating software.

Windows goes through winget; Linux through apt (the server agent's world). Both
are treated as *trusted sources*, which is what lets the policy layer install a
free dependency for an explicitly requested task without interrupting the user —
while an install that costs money, comes from elsewhere, or is hard to undo still
stops for a yes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from typing import Any

from ..errors import ExecutionError, Unsupported
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, app_key

_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(GB|MB|KB)", re.IGNORECASE)


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "package_search",
        "Szuka programu w repozytorium systemowym (winget / apt).",
        params={
            "query": ParamSpec("string", "Nazwa programu", required=True),
            "limit": ParamSpec("int", "Liczba wyników", default=10),
        },
        effects=[Effect.READ, Effect.NETWORK],
        category="packages",
        timeout=180.0,
    )
    async def package_search(
        ctx: ToolContext, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        manager = _manager()
        if manager == "winget":
            code, out, err = await _exec(
                ["winget", "search", query, "--accept-source-agreements", "--disable-interactivity"]
            )
            if code != 0:
                raise ExecutionError(f"winget search: {err or out}")
            return _parse_winget_table(out)[:limit]
        code, out, err = await _exec(["apt-cache", "search", query])
        if code != 0:
            raise ExecutionError(f"apt-cache: {err or out}")
        rows = []
        for line in out.splitlines()[:limit]:
            name, _, summary = line.partition(" - ")
            rows.append({"id": name.strip(), "name": name.strip(), "summary": summary.strip()})
        return rows

    @registry.tool(
        "package_install",
        "Instaluje program z repozytorium systemowego.",
        params={
            "package": ParamSpec("string", "Identyfikator paczki", required=True),
            "version": ParamSpec("string", "Konkretna wersja", default=""),
        },
        effects=[Effect.INSTALL, Effect.NETWORK, Effect.EXEC, Effect.ELEVATE],
        category="packages",
        timeout=1800.0,
        trusted_source=True,
        resources=lambda p: [app_key("package-manager")],
        estimate=lambda p: (0, 0.0),
        examples=("package_install(package='Git.Git')",),
    )
    async def package_install(
        ctx: ToolContext, package: str, version: str = ""
    ) -> dict[str, Any]:
        manager = _manager()
        ctx.progress(f"Instaluję {package} ({manager})")
        if manager == "winget":
            argv = [
                "winget", "install", "--id", package, "--exact", "--silent",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity",
            ]
            if version:
                argv += ["--version", version]
        else:
            target = f"{package}={version}" if version else package
            argv = ["apt-get", "install", "-y", target]
        code, out, err = await _exec(argv, timeout=1800)
        if code != 0:
            raise ExecutionError(f"Instalacja {package} nie powiodła się: {(err or out)[:600]}")
        return {"package": package, "version": version or "latest", "manager": manager}

    @registry.tool(
        "package_upgrade",
        "Aktualizuje program albo wszystkie dostępne aktualizacje.",
        params={
            "package": ParamSpec("string", "Paczka (puste = wszystkie)", default=""),
        },
        effects=[Effect.INSTALL, Effect.NETWORK, Effect.EXEC, Effect.ELEVATE],
        category="packages",
        timeout=3600.0,
        resources=lambda p: [app_key("package-manager")],
    )
    async def package_upgrade(ctx: ToolContext, package: str = "") -> dict[str, Any]:
        manager = _manager()
        if manager == "winget":
            argv = ["winget", "upgrade", "--silent", "--accept-package-agreements",
                    "--accept-source-agreements", "--disable-interactivity"]
            argv += ["--id", package, "--exact"] if package else ["--all"]
        else:
            argv = ["apt-get", "install", "-y", "--only-upgrade", package] if package \
                else ["apt-get", "upgrade", "-y"]
        code, out, err = await _exec(argv, timeout=3600)
        if code != 0:
            raise ExecutionError(f"Aktualizacja nie powiodła się: {(err or out)[:600]}")
        return {"package": package or "all", "manager": manager, "output": out[-4000:]}

    @registry.tool(
        "package_list_installed",
        "Wypisuje zainstalowane programy.",
        params={"query": ParamSpec("string", "Filtr nazwy", default="")},
        effects=[Effect.READ],
        category="packages",
        timeout=300.0,
    )
    async def package_list_installed(
        ctx: ToolContext, query: str = ""
    ) -> list[dict[str, Any]]:
        manager = _manager()
        if manager == "winget":
            code, out, err = await _exec(
                ["winget", "list", "--accept-source-agreements", "--disable-interactivity"]
            )
            if code != 0:
                raise ExecutionError(f"winget list: {err or out}")
            rows = _parse_winget_table(out)
        else:
            code, out, err = await _exec(["dpkg-query", "-W", "-f=${Package}\\t${Version}\\n"])
            if code != 0:
                raise ExecutionError(f"dpkg-query: {err or out}")
            rows = []
            for line in out.splitlines():
                name, _, ver = line.partition("\t")
                rows.append({"id": name, "name": name, "version": ver})
        if query:
            needle = query.lower()
            rows = [r for r in rows if needle in str(r.get("name", "")).lower()
                    or needle in str(r.get("id", "")).lower()]
        return rows


def _manager() -> str:
    if sys.platform == "win32":
        if shutil.which("winget") is None:
            raise Unsupported(
                "Brak winget — zainstaluj App Installer z Microsoft Store"
            )
        return "winget"
    if shutil.which("apt-get"):
        return "apt"
    raise Unsupported("Nie znalazłem obsługiwanego menedżera paczek")


async def _exec(argv: list[str], *, timeout: int = 600) -> tuple[int, str, str]:
    binary = shutil.which(argv[0])
    if binary is None:
        raise Unsupported(f"Brak polecenia {argv[0]}")
    needs_sudo = argv[0] in {"apt-get", "dpkg"} and shutil.which("sudo") and _not_root()
    full = ([shutil.which("sudo") or "sudo", "-n", binary, *argv[1:]]
            if needs_sudo else [binary, *argv[1:]])
    process = await asyncio.create_subprocess_exec(
        *full,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"DEBIAN_FRONTEND": "noninteractive", "PATH": _path()},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ExecutionError(f"{argv[0]} przekroczyło {timeout} s") from None
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _not_root() -> bool:
    import os

    return hasattr(os, "geteuid") and os.geteuid() != 0


def _path() -> str:
    import os

    return os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")


def _parse_winget_table(output: str) -> list[dict[str, Any]]:
    """winget prints a fixed-width table; the header row gives the column offsets."""
    lines = [line for line in output.splitlines() if line.strip()]
    header_index = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith(("Name", "Nazwa"))),
        None,
    )
    if header_index is None:
        return []
    header = lines[header_index]
    columns = _column_offsets(header)
    rows: list[dict[str, Any]] = []
    for line in lines[header_index + 1:]:
        if set(line.strip()) <= {"-", "─"}:
            continue
        values = [line[start:end].strip() for start, end in columns]
        if not values or not values[0]:
            continue
        row = {
            "name": values[0],
            "id": values[1] if len(values) > 1 else values[0],
            "version": values[2] if len(values) > 2 else "",
        }
        rows.append(row)
    return rows


def _column_offsets(header: str) -> list[tuple[int, int | None]]:
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    merged: list[int] = []
    for start in starts:
        if merged and start - merged[-1] < 3:
            continue
        merged.append(start)
    return [
        (start, merged[i + 1] if i + 1 < len(merged) else None)
        for i, start in enumerate(merged)
    ]


def parse_size(text: str) -> int:
    """Turn "1.2 GB" into bytes — used to estimate downloads for the notice gate."""
    match = _SIZE.search(text)
    if not match:
        return 0
    value = float(match.group(1).replace(",", "."))
    unit = match.group(2).upper()
    return int(value * {"KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit])


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = ["parse_size", "register"]
