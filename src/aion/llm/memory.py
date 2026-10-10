"""Conversation memory: short-term dialog history and long-term facts about the user."""

from __future__ import annotations

import time
from collections import deque

from aion.llm.base import Message
from aion.storage import Database


class ShortTermMemory:
    """Last N exchanges; forgotten after a long pause (a new conversation starts)."""

    def __init__(self, max_turns: int = 10, idle_reset_s: float = 30 * 60) -> None:
        self.max_turns = max_turns
        self.idle_reset_s = idle_reset_s
        self._turns: deque[list[Message]] = deque()
        self._last = 0.0

    def messages(self) -> list[Message]:
        if self._last and time.monotonic() - self._last > self.idle_reset_s:
            self._turns.clear()
        return [m for turn in self._turns for m in turn]

    def add_turn(self, messages: list[Message]) -> None:
        """One user request with everything that answered it (tool calls included)."""
        self._turns.append(messages)
        while len(self._turns) > self.max_turns:
            self._turns.popleft()
        self._last = time.monotonic()

    def clear(self) -> None:
        self._turns.clear()


class FactStore:
    """Long-term facts about the user (SQLite ``facts`` table)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, text: str) -> bool:
        text = text.strip().rstrip(".")
        if not text:
            return False
        existing = await self.db.fetchone("SELECT id FROM facts WHERE text = ?", (text,))
        if existing:
            return False
        await self.db.execute("INSERT INTO facts (text) VALUES (?)", (text,))
        return True

    async def all(self, limit: int = 50) -> list[str]:
        rows = await self.db.fetchall("SELECT text FROM facts ORDER BY id DESC LIMIT ?", (limit,))
        return [r["text"] for r in reversed(rows)]

    async def forget(self, query: str) -> int:
        """Delete facts containing ``query`` (case-insensitive). Returns how many."""
        rows = await self.db.fetchall("SELECT id, text FROM facts")
        victims = [r["id"] for r in rows if query.lower() in r["text"].lower()]
        for fact_id in victims:
            await self.db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        return len(victims)

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM facts")
