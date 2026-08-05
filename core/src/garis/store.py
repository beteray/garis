"""SQLite state store.

One connection per database file, guarded by a lock, WAL enabled. Tasks that
must survive a reboot, the audit trail, memory, approvals and subscriptions all
land here. Migrations are append-only: add a statement, never edit an old one.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from .errors import StoreError

T = TypeVar("T")

SCHEMA: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id            TEXT PRIMARY KEY,
            goal          TEXT NOT NULL,
            criteria      TEXT NOT NULL DEFAULT '[]',
            state         TEXT NOT NULL,
            origin        TEXT NOT NULL DEFAULT 'user',
            target        TEXT NOT NULL DEFAULT 'local',
            plan          TEXT NOT NULL DEFAULT '[]',
            cursor        INTEGER NOT NULL DEFAULT 0,
            result        TEXT,
            report        TEXT,
            error         TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL,
            updated_at    REAL NOT NULL,
            started_at    REAL,
            finished_at   REAL,
            deadline      REAL,
            meta          TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, updated_at);

        -- Step journal: the reason a task can resume mid-flight after a reboot.
        CREATE TABLE IF NOT EXISTS task_steps (
            task_id     TEXT NOT NULL,
            step_key    TEXT NOT NULL,
            ordinal     INTEGER NOT NULL,
            tool        TEXT NOT NULL,
            params      TEXT NOT NULL DEFAULT '{}',
            state       TEXT NOT NULL,
            result      TEXT,
            error       TEXT,
            attempts    INTEGER NOT NULL DEFAULT 0,
            started_at  REAL,
            finished_at REAL,
            PRIMARY KEY (task_id, step_key)
        );

        CREATE TABLE IF NOT EXISTS audit (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            at          REAL NOT NULL,
            task_id     TEXT,
            tool        TEXT NOT NULL,
            intent      TEXT NOT NULL DEFAULT '',
            effects     TEXT NOT NULL DEFAULT '[]',
            decision    TEXT NOT NULL,
            rule        TEXT,
            params      TEXT NOT NULL DEFAULT '{}',
            outcome     TEXT NOT NULL,
            detail      TEXT,
            duration_ms INTEGER
        );

        CREATE INDEX IF NOT EXISTS audit_at_idx ON audit(at);
        CREATE INDEX IF NOT EXISTS audit_task_idx ON audit(task_id);

        -- Approvals are rows, not in-memory promises: a task may wait for a yes
        -- across an app restart or a reboot, and the yes may arrive from mobile.
        CREATE TABLE IF NOT EXISTS approvals (
            id          TEXT PRIMARY KEY,
            task_id     TEXT,
            tool        TEXT NOT NULL,
            prompt      TEXT NOT NULL,
            effects     TEXT NOT NULL DEFAULT '[]',
            params      TEXT NOT NULL DEFAULT '{}',
            fingerprint TEXT NOT NULL DEFAULT '',
            state       TEXT NOT NULL,
            consumed    INTEGER NOT NULL DEFAULT 0,
            requested_at REAL NOT NULL,
            resolved_at REAL,
            resolved_by TEXT
        );

        CREATE INDEX IF NOT EXISTS approvals_state_idx ON approvals(state, requested_at);
        CREATE INDEX IF NOT EXISTS approvals_fp_idx ON approvals(fingerprint, state);

        -- Memory: subject, tags and body all live inside content_enc. Only
        -- non-revealing metadata stays in the clear, so a stolen database file
        -- shows how much GARIS remembers and when — never what.
        CREATE TABLE IF NOT EXISTS memory (
            id          TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,
            content_enc TEXT NOT NULL,
            scope       TEXT NOT NULL DEFAULT 'permanent',
            source      TEXT NOT NULL DEFAULT 'user',
            confidence  REAL NOT NULL DEFAULT 1.0,
            pinned      INTEGER NOT NULL DEFAULT 0,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            used_at     REAL,
            use_count   INTEGER NOT NULL DEFAULT 0,
            expires_at  REAL
        );

        CREATE INDEX IF NOT EXISTS memory_kind_idx ON memory(kind);
        CREATE INDEX IF NOT EXISTS memory_scope_idx ON memory(scope, updated_at);

        CREATE TABLE IF NOT EXISTS subscriptions (
            id          TEXT PRIMARY KEY,
            topic       TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'interest',
            keywords    TEXT NOT NULL DEFAULT '[]',
            sources     TEXT NOT NULL DEFAULT '[]',
            min_importance INTEGER NOT NULL DEFAULT 3,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  REAL NOT NULL,
            checked_at  REAL,
            meta        TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS feed_items (
            id            TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            title         TEXT NOT NULL,
            url           TEXT NOT NULL DEFAULT '',
            summary       TEXT NOT NULL DEFAULT '',
            importance    INTEGER NOT NULL DEFAULT 1,
            fingerprint   TEXT NOT NULL,
            published_at  REAL,
            seen_at       REAL NOT NULL,
            delivered_at  REAL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS feed_fingerprint_idx
            ON feed_items(subscription_id, fingerprint);

        CREATE TABLE IF NOT EXISTS devices (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            role        TEXT NOT NULL DEFAULT 'server',
            address     TEXT NOT NULL DEFAULT '',
            token_ref   TEXT NOT NULL DEFAULT '',
            facts       TEXT NOT NULL DEFAULT '{}',
            state       TEXT NOT NULL DEFAULT 'unknown',
            created_at  REAL NOT NULL,
            seen_at     REAL
        );

        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        """
        -- Habit observation feeds the proactive layer ("you do this every Monday").
        CREATE TABLE IF NOT EXISTS observations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            at         REAL NOT NULL,
            kind       TEXT NOT NULL,
            signature  TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS observations_sig_idx ON observations(kind, signature);
        """,
    ),
    (
        3,
        """
        -- Effects: one row per thing GARIS did to the world, reserved *before*
        -- it happens. The primary key is what stops two workers, or a worker and
        -- its own restarted self, from doing the same irreversible thing twice.
        CREATE TABLE IF NOT EXISTS effects (
            effect_id      TEXT PRIMARY KEY,
            task_id        TEXT NOT NULL DEFAULT '',
            step_key       TEXT NOT NULL DEFAULT '',
            capability_id  TEXT NOT NULL,
            arguments_hash TEXT NOT NULL DEFAULT '',
            state          TEXT NOT NULL,          -- reserved|done|failed|uncertain
            outcome        TEXT,                   -- JSON, once there is one
            evidence       TEXT NOT NULL DEFAULT '[]',
            reason         TEXT NOT NULL DEFAULT '',
            reserved_at    REAL NOT NULL,
            settled_at     REAL
        );

        CREATE INDEX IF NOT EXISTS effects_task_idx ON effects(task_id, reserved_at);

        -- Outbox: an event is written in the same transaction as the fact it
        -- reports, then published. Nothing here is speculative — a row exists
        -- only because something was committed.
        CREATE TABLE IF NOT EXISTS event_outbox (
            event_id     TEXT PRIMARY KEY,
            topic        TEXT NOT NULL,
            task_id      TEXT NOT NULL DEFAULT '',
            sequence     INTEGER NOT NULL,
            occurred_at  REAL NOT NULL,
            payload      TEXT NOT NULL DEFAULT '{}',
            published_at REAL
        );

        CREATE INDEX IF NOT EXISTS outbox_pending_idx
            ON event_outbox(published_at, sequence);
        """,
    ),
    (
        4,
        """
        -- "Did it fail?" and "did the world move?" are different questions, and
        -- answering the second one with the first is how a half-finished install
        -- gets run again. `state` says how the record was settled; `disposition`
        -- says what happened outside GARIS, and only the second one may license
        -- a retry.
        --
        -- Existing rows migrate to 'unknown' rather than to the old rule that
        -- every failure is repeatable. Their external outcome genuinely is
        -- unknown — nobody recorded it — and inventing a safer-sounding answer
        -- for them would be the same untruth in a new column.
        ALTER TABLE effects ADD COLUMN disposition TEXT NOT NULL DEFAULT 'unknown';

        -- Attempts are numbered and their history is append-only. A retry that
        -- overwrote the previous attempt's evidence would erase the reason it
        -- was retried.
        ALTER TABLE effects ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE effects ADD COLUMN attempts TEXT NOT NULL DEFAULT '[]';

        -- The verifier's whole verdict, stored so a replay hands back what was
        -- actually measured. Reconstructing `goal_met` from `state` is how a
        -- checked failure ("asked for 30%, measured 33%") replays as a success.
        ALTER TABLE effects ADD COLUMN verification TEXT;
        """,
    ),
]

VAULT_SCHEMA: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS secrets (
            name        TEXT PRIMARY KEY,
            value_enc   TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'token',
            note        TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            used_at     REAL,
            use_count   INTEGER NOT NULL DEFAULT 0
        );
        """,
    ),
]


class Database:
    """Thin, thread-safe SQLite wrapper.

    Synchronous by design — every statement here is a local, indexed, sub-millisecond
    operation, and a lock is cheaper and far easier to reason about than an async
    driver. Long work belongs in tools, not in the store. ``run`` exists for the
    rare call that should not share the event loop thread.
    """

    def __init__(self, path: Path | str, schema: Sequence[tuple[int, str]] = tuple(SCHEMA)):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
        self.migrate(schema)

    # --- schema ---

    def migrate(self, schema: Sequence[tuple[int, str]]) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version, script in schema:
                if version <= current:
                    continue
                try:
                    self._conn.executescript(script)
                except sqlite3.Error as exc:  # pragma: no cover - corrupt db only
                    self._conn.rollback()
                    raise StoreError(f"Migracja {version} nie przeszła: {exc}") from exc
                self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
            self._conn.commit()

    # --- transactions ---

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Several statements that must all land, or none of them.

        The reason this exists: "persist the change, then emit the event" has a
        window between the two where a crash leaves a fact nobody was told
        about, or — worse, if the order is reversed — an event about something
        that never committed. Both are written here in one transaction, and the
        outbox is drained afterwards.

        Re-entrant with the instance lock, so a caller already inside one of the
        single-statement helpers cannot deadlock against itself.
        """
        with self._lock:
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    # --- statements ---

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StoreError(f"Zapytanie nie przeszło: {exc}") from exc

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            try:
                self._conn.executemany(sql, rows)
                self._conn.commit()
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StoreError(f"Zapytanie zbiorcze nie przeszło: {exc}") from exc

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    async def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(fn, *args, **kwargs)

    # --- key/value helpers ---

    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


__all__ = ["SCHEMA", "VAULT_SCHEMA", "Database", "dumps", "loads"]
