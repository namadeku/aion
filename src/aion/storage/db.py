"""SQLite storage (aiosqlite): plugin key-value data, user facts, diary, notes, todos."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE TABLE IF NOT EXISTS facts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text    TEXT NOT NULL UNIQUE,
    created REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE TABLE IF NOT EXISTS episodes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started REAL NOT NULL,
    ended   REAL NOT NULL,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS follow_ups (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic   TEXT NOT NULL,
    due     TEXT,                           -- 'YYYY-MM-DD': ask on or after this day
    episode INTEGER,
    closed  INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL DEFAULT 'note',   -- 'note' | 'todo'
    text    TEXT NOT NULL,
    done    INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не открыта")
        return self._conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cursor = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cursor.lastrowid or cursor.rowcount

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, tuple(params)) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    def kv(self, namespace: str) -> KeyValueStore:
        return KeyValueStore(self, namespace)


class KeyValueStore:
    """Per-plugin JSON key-value storage."""

    def __init__(self, db: Database, namespace: str) -> None:
        self._db = db
        self.namespace = namespace

    async def get(self, key: str, default: Any = None) -> Any:
        row = await self._db.fetchone(
            "SELECT value FROM kv WHERE namespace = ? AND key = ?", (self.namespace, key)
        )
        return json.loads(row["value"]) if row else default

    async def set(self, key: str, value: Any) -> None:
        await self._db.execute(
            "INSERT INTO kv (namespace, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(namespace, key) DO UPDATE SET value = excluded.value",
            (self.namespace, key, json.dumps(value, ensure_ascii=False)),
        )

    async def delete(self, key: str) -> None:
        await self._db.execute(
            "DELETE FROM kv WHERE namespace = ? AND key = ?", (self.namespace, key)
        )

    async def keys(self) -> list[str]:
        rows = await self._db.fetchall(
            "SELECT key FROM kv WHERE namespace = ? ORDER BY key", (self.namespace,)
        )
        return [r["key"] for r in rows]

    async def all(self) -> dict[str, Any]:
        rows = await self._db.fetchall(
            "SELECT key, value FROM kv WHERE namespace = ?", (self.namespace,)
        )
        return {r["key"]: json.loads(r["value"]) for r in rows}
