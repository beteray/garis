"""Files and folders."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from ..errors import ExecutionError
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry, file_key

MAX_READ_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".cs", ".c", ".h",
    ".cpp", ".ps1", ".bat", ".cmd", ".sh", ".sql", ".csv", ".xml", ".html", ".css",
}


def register(registry: ToolRegistry) -> None:
    @registry.tool(
        "file_read",
        "Czyta zawartość pliku tekstowego.",
        params={
            "path": ParamSpec("path", "Ścieżka do pliku", required=True),
            "max_bytes": ParamSpec("int", "Limit odczytu", default=MAX_READ_BYTES),
        },
        effects=[Effect.READ],
        category="files",
        resources=lambda p: [file_key(p["path"])],
        examples=("file_read(path='C:/projekt/README.md')",),
    )
    async def file_read(ctx: ToolContext, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
        target = _resolve(path)
        if not target.exists():
            raise ExecutionError(f"Nie ma pliku {target}", retryable=False)
        if target.is_dir():
            raise ExecutionError(f"{target} to katalog, nie plik", retryable=False)
        size = target.stat().st_size
        if size > max_bytes:
            raise ExecutionError(
                f"Plik ma {size} B, limit to {max_bytes} B — użyj file_search albo podnieś limit"
            )
        return await asyncio.to_thread(target.read_text, "utf-8", "replace")

    @registry.tool(
        "file_write",
        "Zapisuje tekst do pliku, tworząc katalogi po drodze. Nadpisuje istniejącą treść.",
        params={
            "path": ParamSpec("path", "Ścieżka do pliku", required=True),
            "content": ParamSpec("string", "Treść do zapisania", required=True),
            "append": ParamSpec("bool", "Dopisz na końcu zamiast nadpisać", default=False),
        },
        effects=[Effect.WRITE],
        category="files",
        resources=lambda p: [file_key(p["path"])],
    )
    async def file_write(
        ctx: ToolContext, path: str, content: str, append: bool = False
    ) -> dict[str, Any]:
        target = _resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"

        def write() -> int:
            with open(target, mode, encoding="utf-8", newline="") as handle:
                return handle.write(content)

        written = await asyncio.to_thread(write)
        return {"path": str(target), "bytes": written, "appended": append}

    @registry.tool(
        "file_list",
        "Wypisuje zawartość katalogu.",
        params={
            "path": ParamSpec("path", "Katalog", required=True),
            "pattern": ParamSpec("string", "Wzorzec glob, np. *.log", default="*"),
            "recursive": ParamSpec("bool", "Przeszukaj podkatalogi", default=False),
            "limit": ParamSpec("int", "Maksymalna liczba pozycji", default=500),
        },
        effects=[Effect.READ],
        category="files",
    )
    async def file_list(
        ctx: ToolContext,
        path: str,
        pattern: str = "*",
        recursive: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        root = _resolve(path)
        if not root.is_dir():
            raise ExecutionError(f"{root} nie jest katalogiem", retryable=False)

        def scan() -> list[dict[str, Any]]:
            iterator = root.rglob(pattern) if recursive else root.glob(pattern)
            out: list[dict[str, Any]] = []
            for entry in iterator:
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                out.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "dir": entry.is_dir(),
                        "bytes": stat.st_size,
                        "modified": stat.st_mtime,
                    }
                )
                if len(out) >= limit:
                    break
            return out

        return await asyncio.to_thread(scan)

    @registry.tool(
        "file_search",
        "Szuka tekstu w plikach pod wskazaną ścieżką i zwraca dopasowane linie.",
        params={
            "path": ParamSpec("path", "Katalog lub plik", required=True),
            "query": ParamSpec("string", "Szukany tekst", required=True),
            "pattern": ParamSpec("string", "Wzorzec nazw plików", default="*"),
            "max_results": ParamSpec("int", "Limit dopasowań", default=100),
        },
        effects=[Effect.READ],
        category="files",
    )
    async def file_search(
        ctx: ToolContext,
        path: str,
        query: str,
        pattern: str = "*",
        max_results: int = 100,
    ) -> list[dict[str, Any]]:
        root = _resolve(path)
        needle = query.lower()

        def scan() -> list[dict[str, Any]]:
            files = [root] if root.is_file() else list(root.rglob(pattern))
            hits: list[dict[str, Any]] = []
            for entry in files:
                if not entry.is_file() or not _looks_textual(entry):
                    continue
                try:
                    with open(entry, encoding="utf-8", errors="replace") as handle:
                        for number, line in enumerate(handle, start=1):
                            if needle in line.lower():
                                hits.append(
                                    {
                                        "path": str(entry),
                                        "line": number,
                                        "text": line.rstrip()[:400],
                                    }
                                )
                                if len(hits) >= max_results:
                                    return hits
                except OSError:
                    continue
            return hits

        return await asyncio.to_thread(scan)

    @registry.tool(
        "file_copy",
        "Kopiuje plik lub katalog.",
        params={
            "source": ParamSpec("path", "Źródło", required=True),
            "destination": ParamSpec("path", "Cel", required=True),
            "overwrite": ParamSpec("bool", "Nadpisz istniejący cel", default=False),
        },
        effects=[Effect.WRITE],
        category="files",
        resources=lambda p: [file_key(p["source"]), file_key(p["destination"])],
    )
    async def file_copy(
        ctx: ToolContext, source: str, destination: str, overwrite: bool = False
    ) -> dict[str, Any]:
        src, dst = _resolve(source), _resolve(destination)
        if not src.exists():
            raise ExecutionError(f"Nie ma {src}", retryable=False)
        if dst.exists() and not overwrite:
            raise ExecutionError(f"{dst} już istnieje", retryable=False)

        def copy() -> None:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=overwrite)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        await asyncio.to_thread(copy)
        return {"source": str(src), "destination": str(dst)}

    @registry.tool(
        "file_move",
        "Przenosi lub zmienia nazwę pliku albo katalogu.",
        params={
            "source": ParamSpec("path", "Źródło", required=True),
            "destination": ParamSpec("path", "Cel", required=True),
        },
        effects=[Effect.WRITE],
        category="files",
        resources=lambda p: [file_key(p["source"]), file_key(p["destination"])],
    )
    async def file_move(ctx: ToolContext, source: str, destination: str) -> dict[str, Any]:
        src, dst = _resolve(source), _resolve(destination)
        if not src.exists():
            raise ExecutionError(f"Nie ma {src}", retryable=False)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.move, str(src), str(dst))
        return {"source": str(src), "destination": str(dst)}

    @registry.tool(
        "file_recycle",
        "Przenosi plik do kosza GARIS-a — odwracalne usunięcie.",
        params={"path": ParamSpec("path", "Co usunąć", required=True)},
        effects=[Effect.WRITE],
        category="files",
        resources=lambda p: [file_key(p["path"])],
    )
    async def file_recycle(ctx: ToolContext, path: str) -> dict[str, Any]:
        """Default deletion route.

        Reversible on purpose: the planner reaches for this, so ordinary cleanup
        never triggers an approval prompt, and a mistake costs nothing.
        """
        target = _resolve(path)
        if not target.exists():
            raise ExecutionError(f"Nie ma {target}", retryable=False)
        bin_dir = ctx.paths.home / "recycle"
        bin_dir.mkdir(parents=True, exist_ok=True)
        import time

        destination = bin_dir / f"{int(time.time())}-{target.name}"
        await asyncio.to_thread(shutil.move, str(target), str(destination))
        return {"path": str(target), "recycled_to": str(destination)}

    @registry.tool(
        "file_delete",
        "Trwale usuwa plik lub katalog. Nieodwracalne.",
        params={
            "path": ParamSpec("path", "Co usunąć", required=True),
            "recursive": ParamSpec("bool", "Usuń katalog z zawartością", default=False),
        },
        effects=[Effect.DELETE_PERMANENT],
        category="files",
        reversible=False,
        danger_note="Tego nie da się odzyskać z kosza.",
        resources=lambda p: [file_key(p["path"])],
    )
    async def file_delete(
        ctx: ToolContext, path: str, recursive: bool = False
    ) -> dict[str, Any]:
        target = _resolve(path)
        if not target.exists():
            raise ExecutionError(f"Nie ma {target}", retryable=False)

        def remove() -> None:
            if target.is_dir():
                if not recursive:
                    raise ExecutionError(
                        f"{target} to katalog — ustaw recursive=true", retryable=False
                    )
                shutil.rmtree(target)
            else:
                target.unlink()

        await asyncio.to_thread(remove)
        return {"path": str(target), "deleted": True}

    @registry.tool(
        "dir_create",
        "Tworzy katalog.",
        params={"path": ParamSpec("path", "Ścieżka katalogu", required=True)},
        effects=[Effect.WRITE],
        category="files",
    )
    async def dir_create(ctx: ToolContext, path: str) -> dict[str, Any]:
        target = _resolve(path)
        target.mkdir(parents=True, exist_ok=True)
        return {"path": str(target)}

    @registry.tool(
        "file_info",
        "Zwraca metadane pliku lub katalogu.",
        params={"path": ParamSpec("path", "Ścieżka", required=True)},
        effects=[Effect.READ],
        category="files",
    )
    async def file_info(ctx: ToolContext, path: str) -> dict[str, Any]:
        target = _resolve(path)
        if not target.exists():
            return {"path": str(target), "exists": False}
        stat = target.stat()
        return {
            "path": str(target),
            "exists": True,
            "dir": target.is_dir(),
            "bytes": stat.st_size,
            "modified": stat.st_mtime,
            "created": stat.st_ctime,
            "readonly": not os.access(target, os.W_OK),
        }


def _resolve(path: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(path.strip()))
    return Path(expanded).resolve()


def _looks_textual(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return False
        with open(path, "rb") as handle:
            return b"\0" not in handle.read(2048)
    except OSError:
        return False


__all__ = ["register"]
