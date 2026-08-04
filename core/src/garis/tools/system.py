"""Processes, services, registry, network, machine facts.

Cross-platform where it costs nothing (process list, disk usage), Windows-specific
where it matters (registry, services, firewall). Tools that cannot work on this
host declare their platform and are hidden from the planner instead of failing
halfway through a plan.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from typing import Any

from ..errors import ExecutionError, Unsupported
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, app_key

WINDOWS = ("win32",)


def register(registry: ToolRegistry) -> None:
    # ----------------------------------------------------------------- facts

    @registry.tool(
        "system_info",
        "Zwraca informacje o komputerze: system, procesor, pamięć, dyski, sieć.",
        params={},
        effects=[Effect.READ],
        category="system",
    )
    async def system_info(ctx: ToolContext) -> dict[str, Any]:
        info: dict[str, Any] = {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER", ""),
        }
        info["disks"] = await asyncio.to_thread(_disks)
        info["memory"] = await asyncio.to_thread(_memory)
        return info

    @registry.tool(
        "disk_usage",
        "Sprawdza zajętość dysku dla podanej ścieżki.",
        params={"path": ParamSpec("path", "Ścieżka lub litera dysku", default="")},
        effects=[Effect.READ],
        category="system",
    )
    async def disk_usage(ctx: ToolContext, path: str = "") -> dict[str, Any]:
        target = path or ("C:\\" if sys.platform == "win32" else "/")
        return await asyncio.to_thread(_disk_bytes, target)

    @registry.tool(
        "memory_usage",
        "Sprawdza, ile pamięci RAM jest zajęte i ile zostało wolnej.",
        params={},
        effects=[Effect.READ],
        category="system",
    )
    async def memory_usage(ctx: ToolContext) -> dict[str, Any]:
        """Measured bytes, or nothing at all.

        Raises rather than guessing when the host cannot be read: a made-up
        number is worse than an honest "this machine will not tell me".
        """
        measured = await asyncio.to_thread(_memory_bytes)
        if not measured:
            raise Unsupported(
                "Nie umiem odczytać pamięci na tym systemie (brak psutil i /proc/meminfo)"
            )
        return measured

    @registry.tool(
        "current_time",
        "Podaje bieżącą datę i godzinę na tym komputerze.",
        params={},
        effects=[Effect.READ],
        category="system",
    )
    async def current_time(ctx: ToolContext) -> dict[str, Any]:
        now = datetime.now().astimezone()
        return {
            "local": now.strftime("%H:%M, %d.%m.%Y"),
            "iso": now.isoformat(timespec="seconds"),
            "timezone": now.tzname() or "",
            "unix": now.timestamp(),
        }

    # ------------------------------------------------------------- processes

    @registry.tool(
        "process_list",
        "Wypisuje działające procesy, opcjonalnie filtrując po nazwie.",
        params={
            "name": ParamSpec("string", "Fragment nazwy procesu", default=""),
            "limit": ParamSpec("int", "Maksymalna liczba wyników", default=100),
        },
        effects=[Effect.READ],
        category="system",
    )
    async def process_list(
        ctx: ToolContext, name: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = await asyncio.to_thread(_processes)
        needle = name.lower()
        if needle:
            rows = [r for r in rows if needle in r["name"].lower()]
        return rows[:limit]

    @registry.tool(
        "process_kill",
        "Zamyka proces o podanym PID albo nazwie.",
        params={
            "pid": ParamSpec("int", "Identyfikator procesu", default=0),
            "name": ParamSpec("string", "Nazwa procesu", default=""),
            "force": ParamSpec("bool", "Wymuś zamknięcie", default=False),
        },
        effects=[Effect.EXEC, Effect.SYSTEM_CONFIG],
        category="system",
        resources=lambda p: [app_key(str(p.get("name") or p.get("pid") or "process"))],
    )
    async def process_kill(
        ctx: ToolContext, pid: int = 0, name: str = "", force: bool = False
    ) -> dict[str, Any]:
        if not pid and not name:
            raise ExecutionError("Podaj pid albo name", retryable=False)
        if sys.platform == "win32":
            argv = ["taskkill"] + (["/F"] if force else [])
            argv += ["/PID", str(pid)] if pid else ["/IM", name]
        else:
            signal = "-9" if force else "-15"
            argv = ["kill", signal, str(pid)] if pid else ["pkill", signal, name]
        code, out, err = await _exec(argv)
        if code != 0:
            raise ExecutionError(f"Nie udało się zamknąć procesu: {err or out}")
        return {"pid": pid or None, "name": name or None, "forced": force}

    # -------------------------------------------------------------- services

    @registry.tool(
        "service_list",
        "Wypisuje usługi systemowe i ich stan.",
        params={"name": ParamSpec("string", "Fragment nazwy usługi", default="")},
        effects=[Effect.READ],
        category="system",
    )
    async def service_list(ctx: ToolContext, name: str = "") -> list[dict[str, Any]]:
        if sys.platform == "win32":
            script = (
                "Get-Service | Select-Object Name,DisplayName,Status,StartType "
                "| ConvertTo-Json -Compress"
            )
            code, out, err = await _exec(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
            )
            if code != 0:
                raise ExecutionError(f"Nie mogę odczytać usług: {err}")
            parsed = json.loads(out or "[]")
            rows = parsed if isinstance(parsed, list) else [parsed]
            services = [
                {
                    "name": r.get("Name", ""),
                    "display_name": r.get("DisplayName", ""),
                    "state": str(r.get("Status", "")),
                    "start_type": str(r.get("StartType", "")),
                }
                for r in rows
            ]
        else:
            code, out, err = await _exec(
                ["systemctl", "list-units", "--type=service", "--all", "--no-pager",
                 "--plain", "--no-legend"]
            )
            if code != 0:
                raise Unsupported("Brak systemd — nie mogę wypisać usług")
            services = []
            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    services.append(
                        {
                            "name": parts[0],
                            "display_name": parts[4] if len(parts) > 4 else parts[0],
                            "state": parts[3],
                            "start_type": parts[1],
                        }
                    )
        needle = name.lower()
        if needle:
            services = [
                s for s in services
                if needle in s["name"].lower() or needle in s["display_name"].lower()
            ]
        return services

    @registry.tool(
        "service_control",
        "Startuje, zatrzymuje lub restartuje usługę systemową.",
        params={
            "name": ParamSpec("string", "Nazwa usługi", required=True),
            "action": ParamSpec(
                "string", "Co zrobić", required=True,
                choices=("start", "stop", "restart"),
            ),
        },
        effects=[Effect.SYSTEM_CONFIG, Effect.ELEVATE],
        category="system",
        resources=lambda p: [f"service:{str(p['name']).lower()}"],
        timeout=180.0,
    )
    async def service_control(ctx: ToolContext, name: str, action: str) -> dict[str, Any]:
        if sys.platform == "win32":
            verb = {"start": "Start-Service", "stop": "Stop-Service",
                    "restart": "Restart-Service"}[action]
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f"{verb} -Name '{name}' -ErrorAction Stop"]
        else:
            argv = ["systemctl", action, name]
        code, out, err = await _exec(argv)
        if code != 0:
            raise ExecutionError(f"Usługa {name}: {action} nie powiodło się — {err or out}")
        return {"service": name, "action": action, "ok": True}

    # -------------------------------------------------------------- registry

    @registry.tool(
        "registry_read",
        "Czyta wartość z rejestru Windows.",
        params={
            "key": ParamSpec("string", "Klucz, np. HKCU\\Software\\Garis", required=True),
            "name": ParamSpec("string", "Nazwa wartości (puste = domyślna)", default=""),
        },
        effects=[Effect.READ],
        platforms=WINDOWS,
        category="system",
    )
    async def registry_read(ctx: ToolContext, key: str, name: str = "") -> dict[str, Any]:
        reg = _winreg()

        root, subkey = _split_registry(key)

        def read() -> Any:
            with reg.OpenKey(root, subkey) as handle:
                value, _ = reg.QueryValueEx(handle, name)
                return value

        try:
            value = await asyncio.to_thread(read)
        except FileNotFoundError:
            return {"key": key, "name": name, "exists": False}
        return {"key": key, "name": name, "exists": True, "value": value}

    @registry.tool(
        "registry_write",
        "Zapisuje wartość w rejestrze Windows.",
        params={
            "key": ParamSpec("string", "Klucz", required=True),
            "name": ParamSpec("string", "Nazwa wartości", required=True),
            "value": ParamSpec("string", "Wartość", required=True),
            "type": ParamSpec("string", "Typ", default="string",
                              choices=("string", "dword", "expand_string")),
        },
        effects=[Effect.SYSTEM_CONFIG],
        platforms=WINDOWS,
        category="system",
        resources=lambda p: [f"registry:{str(p['key']).lower()}"],
    )
    async def registry_write(
        ctx: ToolContext, key: str, name: str, value: str, type: str = "string"
    ) -> dict[str, Any]:
        reg = _winreg()

        root, subkey = _split_registry(key)
        kinds = {
            "string": reg.REG_SZ,
            "dword": reg.REG_DWORD,
            "expand_string": reg.REG_EXPAND_SZ,
        }
        payload: Any = int(value) if type == "dword" else value

        def write() -> None:
            with reg.CreateKeyEx(root, subkey, 0, reg.KEY_SET_VALUE) as handle:
                reg.SetValueEx(handle, name, 0, kinds[type], payload)

        await asyncio.to_thread(write)
        return {"key": key, "name": name, "written": True}

    # --------------------------------------------------------------- network

    @registry.tool(
        "network_info",
        "Pokazuje konfigurację sieci i otwarte połączenia.",
        params={"listening_only": ParamSpec("bool", "Tylko nasłuchujące porty", default=True)},
        effects=[Effect.READ],
        category="network",
    )
    async def network_info(ctx: ToolContext, listening_only: bool = True) -> dict[str, Any]:
        if sys.platform == "win32":
            argv = ["netstat", "-ano"]
        else:
            argv = ["ss", "-tulnp"] if shutil.which("ss") else ["netstat", "-tulnp"]
        code, out, err = await _exec(argv)
        lines = out.splitlines() if code == 0 else []
        if listening_only:
            lines = [line for line in lines if "LISTEN" in line.upper()]
        return {
            "hostname": socket.gethostname(),
            "addresses": await asyncio.to_thread(_addresses),
            "connections": lines[:200],
            "error": err if code != 0 else "",
        }

    @registry.tool(
        "firewall_rule",
        "Dodaje lub usuwa regułę zapory Windows.",
        params={
            "name": ParamSpec("string", "Nazwa reguły", required=True),
            "action": ParamSpec("string", "Operacja", required=True,
                                choices=("add", "remove")),
            "port": ParamSpec("int", "Numer portu", default=0),
            "protocol": ParamSpec("string", "Protokół", default="TCP",
                                  choices=("TCP", "UDP")),
            "direction": ParamSpec("string", "Kierunek", default="in",
                                   choices=("in", "out")),
            "allow": ParamSpec("bool", "Zezwolić (fałsz = blokować)", default=True),
        },
        effects=[Effect.SYSTEM_CONFIG, Effect.ELEVATE],
        platforms=WINDOWS,
        category="network",
        resources=lambda p: [f"firewall:{str(p['name']).lower()}"],
    )
    async def firewall_rule(
        ctx: ToolContext,
        name: str,
        action: str,
        port: int = 0,
        protocol: str = "TCP",
        direction: str = "in",
        allow: bool = True,
    ) -> dict[str, Any]:
        if action == "remove":
            argv = ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"]
        else:
            if not port:
                raise ExecutionError("Do dodania reguły potrzebny jest port", retryable=False)
            argv = [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={name}",
                f"dir={direction}",
                f"action={'allow' if allow else 'block'}",
                f"protocol={protocol}",
                f"localport={port}",
            ]
        code, out, err = await _exec(argv)
        if code != 0:
            raise ExecutionError(f"netsh: {err or out}")
        return {"rule": name, "action": action, "ok": True}


# --------------------------------------------------------------------- helpers


async def _exec(argv: list[str]) -> tuple[int, str, str]:
    binary = shutil.which(argv[0])
    if binary is None:
        raise Unsupported(f"Brak polecenia {argv[0]} na tym systemie")
    process = await asyncio.create_subprocess_exec(
        binary, *argv[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _processes() -> list[dict[str, Any]]:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return _processes_fallback()
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "cpu_percent"]):
        try:
            info = proc.info
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name") or "",
                    "user": info.get("username") or "",
                    "memory_mb": round((info.get("memory_info").rss if info.get("memory_info")
                                        else 0) / 1024**2, 1),
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                }
            )
        except Exception:
            continue
    rows.sort(key=lambda r: -r["memory_mb"])
    return rows


def _processes_fallback() -> list[dict[str, Any]]:
    """No psutil: shell out. Keeps process listing working on a bare install."""
    if sys.platform == "win32":
        argv = ["tasklist", "/fo", "csv", "/nh"]
    else:
        argv = ["ps", "-eo", "pid,comm,user,rss", "--no-headers"]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in out.splitlines():
        if sys.platform == "win32":
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) < 5:
                continue
            memory = parts[4].replace("\u00a0", "").replace(" K", "").replace(",", "")
            rows.append(
                {
                    "pid": int(parts[1]) if parts[1].isdigit() else 0,
                    "name": parts[0],
                    "user": "",
                    "memory_mb": round(int(memory) / 1024, 1) if memory.isdigit() else 0.0,
                    "cpu_percent": 0.0,
                }
            )
        else:
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            rows.append(
                {
                    "pid": int(parts[0]) if parts[0].isdigit() else 0,
                    "name": parts[1],
                    "user": parts[2],
                    "memory_mb": round(int(parts[3]) / 1024, 1) if parts[3].isdigit() else 0.0,
                    "cpu_percent": 0.0,
                }
            )
    rows.sort(key=lambda r: -r["memory_mb"])
    return rows


def _disks() -> list[dict[str, Any]]:
    mounts: list[str] = []
    if sys.platform == "win32":
        mounts = [f"{letter}:\\" for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                  if os.path.exists(f"{letter}:\\")]
    else:
        mounts = ["/"]
        for candidate in ("/home", "/var", "/mnt"):
            if os.path.ismount(candidate):
                mounts.append(candidate)
    out: list[dict[str, Any]] = []
    for mount in mounts:
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        out.append(
            {
                "mount": mount,
                "total_gb": round(usage.total / 1024**3, 1),
                "free_gb": round(usage.free / 1024**3, 1),
                "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0,
            }
        )
    return out


def _disk_bytes(target: str) -> dict[str, Any]:
    """Every disk number, each one named for what it actually measures.

    Four different quantities used to be reported as three, and the shapes
    overlapped: a container run showed 252 GB total, 24,1 GB free and 5% used —
    all three true, none of them about the same thing. `total - free` is 90% of
    the disk, not 5%, and a person reading the sentence cannot reconcile them.

    What is going on: on this host 239 GB is free *on the filesystem*, but only
    24 GB of it is available to this user — the rest is a quota. The 5% was
    `blocks in use ÷ total`, which answers "how full is the disk" while `free`
    answers "how much may I write". Both are worth knowing and neither may stand
    in for the other, so all of them are returned, and the one pair that must
    agree — `used`, `free` and `percent_used` — is derived from a single
    subtraction:

        used = total - free_available
        percent_used = used / total

    Windows uses `GetDiskFreeSpaceExW`, which draws exactly the same distinction
    (`lpFreeBytesAvailableToCaller` versus `lpTotalNumberOfFreeBytes`) and is
    what quota-aware answers on Windows have to come from.
    """
    total, filesystem_free, free_available = _disk_raw(target)
    filesystem_used = max(total - filesystem_free, 0)
    reserved = max(filesystem_free - free_available, 0)
    used = max(total - free_available, 0)
    return {
        "path": target,
        "total": total,
        #: Writable by whoever is asking — the number that answers "how much
        #: room have I got left".
        "free": free_available,
        "free_available": free_available,
        #: Unused on the filesystem, including anything reserved from this user.
        "filesystem_free": filesystem_free,
        #: Occupied by files. Smaller than `used` wherever a quota applies.
        "filesystem_used": filesystem_used,
        #: Free on the volume but not available here: quota, root reservation,
        #: or a container limit. `used - filesystem_used` by construction.
        "reserved": reserved,
        "used": used,
        "percent_used": round(used / total * 100, 1) if total else 0.0,
    }


def _disk_raw(target: str) -> tuple[int, int, int]:
    """(total, free on the filesystem, free to this caller), in bytes."""
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        import ctypes

        free_to_caller = ctypes.c_ulonglong(0)
        total_bytes = ctypes.c_ulonglong(0)
        total_free = ctypes.c_ulonglong(0)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(target),
            ctypes.byref(free_to_caller),
            ctypes.byref(total_bytes),
            ctypes.byref(total_free),
        )
        if not ok:
            raise ExecutionError(
                f"Windows nie podał zajętości dysku dla {target} "
                f"(GetDiskFreeSpaceExW, błąd {ctypes.GetLastError()})"  # type: ignore[attr-defined]
            )
        return total_bytes.value, total_free.value, free_to_caller.value

    stat = os.statvfs(target)
    block = stat.f_frsize or stat.f_bsize
    return stat.f_blocks * block, stat.f_bfree * block, stat.f_bavail * block


def _memory_bytes() -> dict[str, Any]:
    """Total and available RAM in bytes, measured — empty when unmeasurable.

    psutil first because it is right everywhere; /proc/meminfo second so a Linux
    box without psutil still answers. Nothing is estimated: an empty dict means
    the caller must say it does not know.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        virtual = psutil.virtual_memory()
        return {
            "total": int(virtual.total),
            "available": int(virtual.available),
            "used": int(virtual.total - virtual.available),
            "percent_used": round(virtual.percent, 1),
        }

    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            fields = {}
            for line in handle:
                name, _, rest = line.partition(":")
                value = rest.strip().split(" ")[0]
                if value.isdigit():
                    fields[name.strip()] = int(value) * 1024  # kB in the file
    except OSError:
        return {}

    total = fields.get("MemTotal")
    available = fields.get("MemAvailable", fields.get("MemFree"))
    if not total or available is None:
        return {}
    return {
        "total": total,
        "available": available,
        "used": total - available,
        "percent_used": round((total - available) / total * 100, 1),
    }


def _memory() -> dict[str, Any]:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return {}
    virtual = psutil.virtual_memory()
    return {
        "total_gb": round(virtual.total / 1024**3, 1),
        "available_gb": round(virtual.available / 1024**3, 1),
        "percent_used": virtual.percent,
    }


def _addresses() -> list[str]:
    try:
        host = socket.gethostname()
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _winreg() -> Any:
    """The winreg module, typed as Any.

    It only exists on Windows, so a type checker running anywhere else cannot see
    its members. Reaching for it through one accessor keeps the rest of this file
    honestly typed instead of carrying per-line ignores that mean the opposite
    thing on the platform this code actually runs on.
    """
    import winreg

    return winreg


def _split_registry(key: str) -> tuple[Any, str]:
    reg = _winreg()

    roots = {
        "HKCU": reg.HKEY_CURRENT_USER,
        "HKEY_CURRENT_USER": reg.HKEY_CURRENT_USER,
        "HKLM": reg.HKEY_LOCAL_MACHINE,
        "HKEY_LOCAL_MACHINE": reg.HKEY_LOCAL_MACHINE,
        "HKCR": reg.HKEY_CLASSES_ROOT,
        "HKU": reg.HKEY_USERS,
    }
    cleaned = key.replace("/", "\\").strip("\\")
    head, _, rest = cleaned.partition("\\")
    root = roots.get(head.upper())
    if root is None:
        raise ExecutionError(f"Nieznany katalog rejestru: {head}", retryable=False)
    return root, rest


__all__ = ["register"]
