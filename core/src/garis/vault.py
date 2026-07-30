"""Credential vault.

Separate database, separate derived key, separate API from memory. The rule from
the spec is absolute: passwords, tokens and API keys are never stored as ordinary
memory. They live here and are referenced elsewhere as ``vault://name``.

Secret *values* never enter a model prompt, a plan, an event or the audit log.
Tools receive the reference; the runtime substitutes the value at the last
possible moment, immediately before the handler runs.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .crypto import SecretBox
from .errors import StoreError
from .store import VAULT_SCHEMA, Database

REF_PREFIX = "vault://"


@dataclass(frozen=True, slots=True)
class SecretInfo:
    """Everything about a secret except the secret."""

    name: str
    kind: str
    note: str
    created_at: float
    updated_at: float
    used_at: float | None
    use_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "used_at": self.used_at,
            "use_count": self.use_count,
            "ref": f"{REF_PREFIX}{self.name}",
        }


def is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(REF_PREFIX)


def ref_name(value: str) -> str:
    return value[len(REF_PREFIX):]


class Vault:
    def __init__(self, db: Database, box: SecretBox) -> None:
        self.db = db
        self._box = box

    @classmethod
    def open(cls, path: Any, box: SecretBox) -> Vault:
        return cls(Database(path, VAULT_SCHEMA), box)

    # ------------------------------------------------------------------- write

    def set(self, name: str, value: str, *, kind: str = "token", note: str = "") -> SecretInfo:
        name = _clean(name)
        now = time.time()
        # Context-bound so a ciphertext copied under another name fails to open.
        token = self._box.encrypt(value, context=f"vault:{name}")
        self.db.execute(
            "INSERT INTO secrets(name, value_enc, kind, note, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET value_enc = excluded.value_enc,"
            " kind = excluded.kind, note = excluded.note, updated_at = excluded.updated_at",
            (name, token, kind, note, now, now),
        )
        info = self.info(name)
        if info is None:  # pragma: no cover - only if the row vanished mid-write
            raise StoreError(f"Nie udało się zapisać sekretu {name!r}")
        return info

    def delete(self, name: str) -> bool:
        cur = self.db.execute("DELETE FROM secrets WHERE name = ?", (_clean(name),))
        return bool(cur.rowcount)

    def rename(self, old: str, new: str) -> None:
        value = self.get(old)
        current = self.info(old)
        self.set(new, value, kind=current.kind if current else "token",
                 note=current.note if current else "")
        self.delete(old)

    # -------------------------------------------------------------------- read

    def get(self, name: str) -> str:
        name = _clean(name)
        row = self.db.one("SELECT value_enc FROM secrets WHERE name = ?", (name,))
        if row is None:
            raise KeyError(name)
        self.db.execute(
            "UPDATE secrets SET used_at = ?, use_count = use_count + 1 WHERE name = ?",
            (time.time(), name),
        )
        return self._box.decrypt_text(row["value_enc"], context=f"vault:{name}")

    def get_optional(self, name: str) -> str | None:
        try:
            return self.get(name)
        except KeyError:
            return None

    def has(self, name: str) -> bool:
        return self.db.one("SELECT 1 FROM secrets WHERE name = ?", (_clean(name),)) is not None

    def info(self, name: str) -> SecretInfo | None:
        row = self.db.one("SELECT * FROM secrets WHERE name = ?", (_clean(name),))
        return _row(row) if row else None

    def list(self) -> Sequence[SecretInfo]:
        """Names and metadata only — this is what the settings screen shows."""
        return [_row(r) for r in self.db.query("SELECT * FROM secrets ORDER BY name")]

    # --------------------------------------------------------------- resolving

    def resolve(self, value: str) -> str:
        """Turn ``vault://openai_api_key`` into the secret. Other strings pass through."""
        if not is_ref(value):
            return value
        name = ref_name(value)
        try:
            return self.get(name)
        except KeyError:
            raise StoreError(
                f"Brak sekretu {name!r} w sejfie",
                user_message=f"Nie mam zapisanego dostępu „{name}”. Podasz go?",
            ) from None

    def resolve_tree(self, params: Any) -> tuple[Any, Sequence[str]]:
        """Substitute every vault reference in a parameter tree.

        Returns the resolved tree and the names used, so the caller can redact
        exactly those values from logs and events.
        """
        used: list[str] = []

        def walk(node: Any) -> Any:
            if isinstance(node, str):
                if is_ref(node):
                    name = ref_name(node)
                    used.append(name)
                    return self.resolve(node)
                return node
            if isinstance(node, dict):
                return {k: walk(v) for k, v in node.items()}
            if isinstance(node, (list, tuple)):
                return [walk(v) for v in node]
            return node

        return walk(params), used

    def close(self) -> None:
        self.db.close()


def _clean(name: str) -> str:
    cleaned = name.strip().lower().replace(" ", "_")
    if not cleaned:
        raise ValueError("Nazwa sekretu nie może być pusta")
    if cleaned.startswith(REF_PREFIX):
        cleaned = ref_name(cleaned)
    return cleaned


def _row(row: Any) -> SecretInfo:
    return SecretInfo(
        name=row["name"],
        kind=row["kind"],
        note=row["note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        used_at=row["used_at"],
        use_count=row["use_count"],
    )


__all__ = ["REF_PREFIX", "SecretInfo", "Vault", "is_ref", "ref_name"]
