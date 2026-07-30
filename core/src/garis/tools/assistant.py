"""GARIS's own faculties: memory, vault, notifications, task introspection.

These are tools rather than direct calls so that "zapamiętaj to" follows exactly
the same audited path as "restart the print spooler" — one execution route, one
place to look when asking what GARIS did and why.
"""

from __future__ import annotations

import time
from typing import Any

from ..errors import ExecutionError
from ..memory import MemoryKind, Scope, looks_like_secret
from ..runtime import Effect, ParamSpec, ToolContext, ToolRegistry

KIND_CHOICES = tuple(k.value for k in MemoryKind)
SCOPE_CHOICES = tuple(s.value for s in Scope)


def register(registry: ToolRegistry) -> None:
    # ------------------------------------------------------------------ memory

    @registry.tool(
        "memory_remember",
        "Zapisuje informację w pamięci długoterminowej GARIS-a.",
        params={
            "content": ParamSpec("string", "Co zapamiętać", required=True),
            "kind": ParamSpec("string", "Rodzaj wpisu", default="fact", choices=KIND_CHOICES),
            "subject": ParamSpec("string", "Czego dotyczy", default=""),
            "tags": ParamSpec("list", "Tagi", default=[]),
            "scope": ParamSpec("string", "Trwałość", default="permanent",
                               choices=SCOPE_CHOICES),
        },
        effects=[Effect.WRITE],
        category="memory",
        examples=("memory_remember(content='Serwer produkcyjny to 10.0.0.5',"
                  " kind='device', subject='serwer produkcyjny')",),
    )
    async def memory_remember(
        ctx: ToolContext,
        content: str,
        kind: str = "fact",
        subject: str = "",
        tags: list[str] | None = None,
        scope: str = "permanent",
    ) -> dict[str, Any]:
        memory = _memory(ctx)
        record = memory.remember(
            content,
            kind=kind,
            subject=subject,
            tags=tuple(tags or ()),
            scope=scope,
            source="agent" if ctx.task_id else "user",
        )
        return {"id": record.id, "subject": record.subject, "scope": record.scope.value}

    @registry.tool(
        "memory_search",
        "Szuka w pamięci GARIS-a.",
        params={
            "query": ParamSpec("string", "Czego szukać", required=True),
            "limit": ParamSpec("int", "Liczba wyników", default=10),
        },
        effects=[Effect.READ],
        category="memory",
    )
    async def memory_search(
        ctx: ToolContext, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        memory = _memory(ctx)
        return [r.to_dict() for r in memory.search(query, limit=limit)]

    @registry.tool(
        "memory_forget",
        "Usuwa wpis z pamięci — po identyfikatorze albo po opisie.",
        params={
            "id": ParamSpec("string", "Identyfikator wpisu", default=""),
            "query": ParamSpec("string", "Opis wpisu do usunięcia", default=""),
        },
        effects=[Effect.DELETE_PERMANENT],
        category="memory",
        reversible=False,
        danger_note="Wpis pamięci zniknie bezpowrotnie.",
    )
    async def memory_forget(
        ctx: ToolContext, id: str = "", query: str = ""
    ) -> dict[str, Any]:
        memory = _memory(ctx)
        if id:
            return {"forgotten": [id] if memory.forget(id) else []}
        if not query:
            raise ExecutionError("Podaj id albo query", retryable=False)
        removed = memory.forget_matching(query)
        return {"forgotten": [r.id for r in removed],
                "subjects": [r.subject for r in removed]}

    @registry.tool(
        "memory_correct",
        "Poprawia istniejący wpis w pamięci.",
        params={
            "id": ParamSpec("string", "Identyfikator wpisu", required=True),
            "content": ParamSpec("string", "Nowa treść", default=""),
            "subject": ParamSpec("string", "Nowy temat", default=""),
        },
        effects=[Effect.WRITE],
        category="memory",
    )
    async def memory_correct(
        ctx: ToolContext, id: str, content: str = "", subject: str = ""
    ) -> dict[str, Any]:
        memory = _memory(ctx)
        try:
            record = memory.update(
                id,
                content=content or None,
                subject=subject or None,
            )
        except KeyError:
            raise ExecutionError(f"Nie ma wpisu {id}", retryable=False) from None
        return record.to_dict()

    # ------------------------------------------------------------------- vault

    @registry.tool(
        "vault_store",
        "Zapisuje hasło, token albo klucz API w zabezpieczonym sejfie.",
        params={
            "name": ParamSpec("string", "Nazwa dostępu", required=True),
            "value": ParamSpec("secret", "Wartość do zapisania", required=True),
            "note": ParamSpec("string", "Opis, do czego służy", default=""),
            "kind": ParamSpec("string", "Rodzaj", default="token",
                              choices=("token", "password", "api_key", "ssh_key", "other")),
        },
        effects=[Effect.CREDENTIALS],
        category="vault",
        danger_note="Poświadczenie trafi do sejfu i będzie używane w Twoim imieniu.",
    )
    async def vault_store(
        ctx: ToolContext, name: str, value: str, note: str = "", kind: str = "token"
    ) -> dict[str, Any]:
        memory = _memory(ctx)
        ref = memory.remember_secret(name, value, note=note, kind=kind)
        return {"ref": ref, "name": name}

    @registry.tool(
        "vault_list",
        "Wypisuje nazwy dostępów w sejfie (bez wartości).",
        params={},
        effects=[Effect.READ],
        category="vault",
    )
    async def vault_list(ctx: ToolContext) -> list[dict[str, Any]]:
        if ctx.vault is None:
            return []
        return [info.to_dict() for info in ctx.vault.list()]

    # ----------------------------------------------------------- communication

    @registry.tool(
        "notify_user",
        "Przekazuje użytkownikowi krótką informację. Warstwa powiadomień decyduje,"
        " czy powiedzieć to teraz, czy zaczekać.",
        params={
            "message": ParamSpec("string", "Treść", required=True),
            "importance": ParamSpec("int", "Ważność 1-5", default=3),
        },
        effects=[Effect.READ],
        category="assistant",
    )
    async def notify_user(
        ctx: ToolContext, message: str, importance: int = 3
    ) -> dict[str, Any]:
        ctx.note(message, importance=max(1, min(5, importance)))
        return {"delivered_to_gate": True, "importance": importance}

    # ------------------------------------------------------- self-introspection

    @registry.tool(
        "task_activity",
        "Podsumowuje, co GARIS robił w ostatnim czasie — na pytanie „co robiłeś?”.",
        params={"minutes": ParamSpec("int", "Ile minut wstecz", default=60)},
        effects=[Effect.READ],
        category="assistant",
    )
    async def task_activity(ctx: ToolContext, minutes: int = 60) -> dict[str, Any]:
        return ctx.runtime.audit.activity_since(time.time() - minutes * 60)

    @registry.tool(
        "capabilities",
        "Wypisuje, co GARIS potrafi na tym komputerze.",
        params={"category": ParamSpec("string", "Filtr kategorii", default="")},
        effects=[Effect.READ],
        category="assistant",
    )
    async def capabilities(ctx: ToolContext, category: str = "") -> dict[str, Any]:
        described = ctx.runtime.describe_capabilities()
        if category:
            described["tools"] = [
                spec.name for spec in ctx.runtime.registry.available()
                if spec.category == category
            ]
        return described

    @registry.tool(
        "check_secret_leak",
        "Sprawdza, czy tekst zawiera poświadczenia, zanim gdziekolwiek trafi.",
        params={"text": ParamSpec("string", "Tekst do sprawdzenia", required=True)},
        effects=[Effect.READ],
        category="assistant",
    )
    async def check_secret_leak(ctx: ToolContext, text: str) -> dict[str, Any]:
        hit = looks_like_secret(text)
        return {"contains_secret": bool(hit), "pattern": hit or ""}


def _memory(ctx: ToolContext):  # type: ignore[no-untyped-def]
    if ctx.memory is None:
        raise ExecutionError("Pamięć nie jest podłączona", retryable=False)
    return ctx.memory


__all__ = ["register"]
