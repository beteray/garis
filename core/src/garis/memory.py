"""User memory — local, encrypted, visible, editable.

What GARIS remembers is the user's property: they can list it, correct it and
delete it. So memory is stored in a way that supports all three (rows with
subjects, tags and provenance) rather than as an opaque embedding blob.

Everything that says anything about the user — body, subject, tags — is inside one
encrypted envelope. The clear-text columns are metadata only (kind, scope,
timestamps), so a stolen database file reveals how much GARIS remembers and when,
never what. Search and subject filters decrypt as they scan; at user scale that is
microseconds and worth far more than a plaintext index.

Credentials are refused outright and routed to the vault.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .crypto import SecretBox
from .errors import SecretNotAllowed
from .events import EventBus, Topic
from .store import Database, dumps, loads
from .text import fold_keep_shape
from .vault import Vault, is_ref


class MemoryKind(StrEnum):
    PREFERENCE = "preference"      # "wolę krótkie odpowiedzi"
    PERSON = "person"              # important people
    PROJECT = "project"
    DEVICE = "device"              # machines, servers, their configuration
    CREDENTIAL_HINT = "credential_hint"   # *where* a login lives, never the login
    APP = "app"                    # which programs the user actually uses
    SOLUTION = "solution"          # "last time this printer broke, X fixed it"
    ROUTINE = "routine"            # recurring behaviour, feeds automations
    FACT = "fact"
    STYLE = "style"                # how to address and talk to the user


class Scope(StrEnum):
    SESSION = "session"        # dropped when the conversation ends
    PROJECT = "project"        # lives as long as the project does
    PERMANENT = "permanent"    # "zapamiętaj to na zawsze"


# Patterns that must never be stored as memory. Deliberately broad: a false
# positive costs one redirect to the vault, a false negative leaks a credential.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),                    # OpenAI-style
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),                # Anthropic
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                   # Google
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),               # GitHub
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),             # Slack
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),                 # Google OAuth
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
    re.compile(
        r"(?:has[łl]o|password|passwd|token|api[_ -]?key|klucz api|secret)"
        r"\s*(?:to|jest|=|:)\s*\S{6,}",
        re.IGNORECASE,
    ),
)


def looks_like_secret(text: str) -> str | None:
    """Return the matched pattern's description, or None. Public for testing."""
    if is_ref(text):
        return None
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


@dataclass(slots=True)
class MemoryRecord:
    id: str
    kind: MemoryKind
    subject: str
    content: str
    preview: str = ""
    tags: tuple[str, ...] = ()
    scope: Scope = Scope.PERMANENT
    source: str = "user"
    confidence: float = 1.0
    pinned: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    used_at: float | None = None
    use_count: int = 0
    expires_at: float | None = None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < time.time()

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind.value,
            "subject": self.subject,
            "preview": self.preview,
            "tags": list(self.tags),
            "scope": self.scope.value,
            "source": self.source,
            "confidence": self.confidence,
            "pinned": self.pinned,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "used_at": self.used_at,
            "use_count": self.use_count,
            "expires_at": self.expires_at,
        }
        if include_content:
            data["content"] = self.content
        return data


class MemoryService:
    def __init__(
        self,
        db: Database,
        box: SecretBox,
        *,
        bus: EventBus | None = None,
        vault: Vault | None = None,
    ) -> None:
        self.db = db
        self._box = box
        self.bus = bus
        self.vault = vault

    # ------------------------------------------------------------------ writing

    def remember(
        self,
        content: str,
        *,
        kind: MemoryKind | str = MemoryKind.FACT,
        subject: str = "",
        tags: Sequence[str] = (),
        scope: Scope | str = Scope.PERMANENT,
        source: str = "user",
        confidence: float = 1.0,
        pinned: bool = False,
        ttl_seconds: float | None = None,
        allow_secret_check_bypass: bool = False,
    ) -> MemoryRecord:
        """Store a memory.

        Raises :class:`SecretNotAllowed` when the content looks like a credential.
        The caller is expected to put it in the vault instead — ``remember_secret``
        does exactly that.
        """
        content = content.strip()
        if not content:
            raise ValueError("Pusta treść pamięci")
        if not allow_secret_check_bypass and (hit := looks_like_secret(content)):
            raise SecretNotAllowed(
                f"Treść wygląda na poświadczenie (wzorzec: {hit}) — miejsce na to jest w sejfie"
            )

        record = MemoryRecord(
            id=uuid.uuid4().hex,
            kind=MemoryKind(kind) if not isinstance(kind, MemoryKind) else kind,
            subject=subject.strip() or _infer_subject(content),
            content=content,
            preview=_preview(content),
            tags=tuple(sorted({t.strip().lower() for t in tags if t.strip()})),
            scope=Scope(scope) if not isinstance(scope, Scope) else scope,
            source=source,
            confidence=confidence,
            pinned=pinned,
            expires_at=time.time() + ttl_seconds if ttl_seconds else None,
        )
        self._insert(record)
        self._notify("remembered", record)
        return record

    def remember_secret(
        self, name: str, value: str, *, note: str = "", kind: str = "token"
    ) -> str:
        """Route a credential to the vault and leave only a pointer in memory."""
        if self.vault is None:
            raise SecretNotAllowed("Sejf nie jest podłączony")
        self.vault.set(name, value, kind=kind, note=note)
        self.remember(
            f"Dostęp „{name}” jest w sejfie ({note or kind}).",
            kind=MemoryKind.CREDENTIAL_HINT,
            subject=name,
            tags=("sejf",),
            source="vault",
            allow_secret_check_bypass=True,
        )
        return f"vault://{name}"

    def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        subject: str | None = None,
        tags: Sequence[str] | None = None,
        scope: Scope | str | None = None,
        confidence: float | None = None,
        pinned: bool | None = None,
    ) -> MemoryRecord:
        record = self.get(memory_id)
        if record is None:
            raise KeyError(memory_id)
        if content is not None:
            content = content.strip()
            if hit := looks_like_secret(content):
                raise SecretNotAllowed(f"Treść wygląda na poświadczenie (wzorzec: {hit})")
            record.content = content
            record.preview = _preview(content)
        if subject is not None:
            record.subject = subject.strip()
        if tags is not None:
            record.tags = tuple(sorted({t.strip().lower() for t in tags if t.strip()}))
        if scope is not None:
            record.scope = Scope(scope) if not isinstance(scope, Scope) else scope
        if confidence is not None:
            record.confidence = confidence
        if pinned is not None:
            record.pinned = pinned
        record.updated_at = time.time()

        self.db.execute(
            "UPDATE memory SET content_enc = ?, scope = ?, confidence = ?, pinned = ?,"
            " updated_at = ? WHERE id = ?",
            (
                self._encrypt(record),
                record.scope.value,
                record.confidence,
                int(record.pinned),
                record.updated_at,
                record.id,
            ),
        )
        self._notify("updated", record)
        return record

    def forget(self, memory_id: str) -> bool:
        record = self.get(memory_id)
        cur = self.db.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
        if cur.rowcount and record is not None:
            self._notify("forgotten", record)
        return bool(cur.rowcount)

    def forget_matching(self, query: str, *, limit: int = 10) -> Sequence[MemoryRecord]:
        """Back "zapomnij o tym" — delete what the phrase clearly points at."""
        found = self.search(query, limit=limit)
        for record in found:
            self.forget(record.id)
        return found

    def pin(self, memory_id: str, pinned: bool = True) -> MemoryRecord:
        return self.update(memory_id, pinned=pinned, scope=Scope.PERMANENT if pinned else None)

    # ------------------------------------------------------------------ reading

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = self.db.one("SELECT * FROM memory WHERE id = ?", (memory_id,))
        return self._decode(row) if row else None

    def list(
        self,
        *,
        kind: MemoryKind | str | None = None,
        scope: Scope | str | None = None,
        subject: str | None = None,
        limit: int = 200,
        include_expired: bool = False,
    ) -> Sequence[MemoryRecord]:
        sql = "SELECT * FROM memory WHERE 1 = 1"
        params: list[Any] = []
        if kind is not None:
            sql += " AND kind = ?"
            params.append(MemoryKind(kind).value)
        if scope is not None:
            sql += " AND scope = ?"
            params.append(Scope(scope).value)
        if not include_expired:
            sql += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(time.time())
        sql += " ORDER BY pinned DESC, updated_at DESC"
        if subject is None:
            sql += " LIMIT ?"
            params.append(limit)
            return [self._decode(r) for r in self.db.query(sql, params)]

        # Subject lives inside the ciphertext, so this filter runs after decrypting.
        needle = _normalise(subject)
        found: list[MemoryRecord] = []
        for row in self.db.query(sql, params):
            record = self._decode(row)
            if needle in _normalise(record.subject):
                found.append(record)
                if len(found) >= limit:
                    break
        return found

    def search(self, query: str, *, limit: int = 20) -> Sequence[MemoryRecord]:
        """Token search over subject, preview and tags, then over decrypted content.

        Plain-text preview covers the common case cheaply; the content pass catches
        the rest. An embedding index can slot in later behind this same signature.
        """
        tokens = [t for t in _normalise(query).split() if len(t) > 2]
        if not tokens:
            return []
        scored: list[tuple[float, MemoryRecord]] = []
        for record in self.list(limit=1000):
            haystack = _normalise(
                " ".join([record.subject, record.preview, " ".join(record.tags)])
            )
            score = sum(2.0 for t in tokens if t in haystack)
            if score == 0:
                if any(t in _normalise(record.content) for t in tokens):
                    score = 1.0
            if score:
                if record.pinned:
                    score += 1.0
                score += min(record.use_count, 5) * 0.1
                scored.append((score, record))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].updated_at))
        found = [record for _, record in scored[:limit]]
        self._touch(found)
        return found

    def context_for(self, goal: str, *, limit: int = 12) -> Sequence[MemoryRecord]:
        """Memories worth putting in front of the planner for this goal."""
        relevant = list(self.search(goal, limit=limit))
        if len(relevant) >= limit:
            return relevant
        seen = {r.id for r in relevant}
        background = [
            *self.list(kind=MemoryKind.STYLE, limit=4),
            *self.list(kind=MemoryKind.PREFERENCE, limit=6),
        ]
        for record in background:
            if record.id not in seen:
                relevant.append(record)
                seen.add(record.id)
        return relevant[:limit]

    def stats(self) -> dict[str, Any]:
        rows = self.db.query("SELECT kind, COUNT(*) AS n FROM memory GROUP BY kind")
        pinned = self.db.one("SELECT COUNT(*) AS n FROM memory WHERE pinned = 1")
        return {
            "total": sum(r["n"] for r in rows),
            "by_kind": {r["kind"]: r["n"] for r in rows},
            "pinned": pinned["n"] if pinned else 0,
        }

    def purge_expired(self) -> int:
        cur = self.db.execute(
            "DELETE FROM memory WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),)
        )
        return cur.rowcount or 0

    def end_session(self) -> int:
        cur = self.db.execute("DELETE FROM memory WHERE scope = ?", (Scope.SESSION.value,))
        return cur.rowcount or 0

    # ------------------------------------------------------------------ private

    def _insert(self, record: MemoryRecord) -> None:
        self.db.execute(
            "INSERT INTO memory(id, kind, content_enc, scope, source,"
            " confidence, pinned, created_at, updated_at, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                record.id,
                record.kind.value,
                self._encrypt(record),
                record.scope.value,
                record.source,
                record.confidence,
                int(record.pinned),
                record.created_at,
                record.updated_at,
                record.expires_at,
            ),
        )

    def _encrypt(self, record: MemoryRecord) -> str:
        # Subject and tags describe the content, so they are encrypted with it
        # rather than kept as searchable clear-text columns.
        payload = dumps(
            {"content": record.content, "subject": record.subject, "tags": list(record.tags)}
        )
        return self._box.encrypt(payload, context=f"memory:{record.id}")

    def _decode(self, row: Any) -> MemoryRecord:
        raw = self._box.decrypt_text(row["content_enc"], context=f"memory:{row['id']}")
        payload = loads(raw, None)
        if isinstance(payload, dict):
            content = payload.get("content", "")
            subject = payload.get("subject", "")
            tags = tuple(payload.get("tags", []))
        else:  # a bare string predates the envelope format
            content, subject, tags = raw, "", ()
        return MemoryRecord(
            id=row["id"],
            kind=MemoryKind(row["kind"]),
            subject=subject,
            content=content,
            preview=_preview(content),
            tags=tags,
            scope=Scope(row["scope"]),
            source=row["source"],
            confidence=row["confidence"],
            pinned=bool(row["pinned"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            used_at=row["used_at"],
            use_count=row["use_count"],
            expires_at=row["expires_at"],
        )

    def _touch(self, records: Sequence[MemoryRecord]) -> None:
        if not records:
            return
        now = time.time()
        self.db.executemany(
            "UPDATE memory SET used_at = ?, use_count = use_count + 1 WHERE id = ?",
            [(now, r.id) for r in records],
        )

    def _notify(self, change: str, record: MemoryRecord) -> None:
        if self.bus is not None:
            self.bus.emit(
                Topic.MEMORY_CHANGED,
                change=change,
                id=record.id,
                kind=record.kind.value,
                subject=record.subject,
                preview=record.preview,
            )


def _preview(content: str, limit: int = 90) -> str:
    """One-line label for listings. Derived after decryption, never persisted."""
    flat = " ".join(content.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _infer_subject(content: str) -> str:
    words = [w for w in re.split(r"[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", content) if w]
    return " ".join(words[:4])[:60]


def _normalise(text: str) -> str:
    return fold_keep_shape(text)


__all__ = [
    "MemoryKind",
    "MemoryRecord",
    "MemoryService",
    "Scope",
    "looks_like_secret",
]
