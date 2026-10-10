"""Conversation and log ring buffers for the UI."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

from aion.core.bus import EventBus
from aion.core.events import AssistantReply, LogRecord, SpeechRecognized


@dataclass
class Entry:
    role: str  # "user" | "assistant"
    text: str
    source: str = ""
    ts: float = field(default_factory=time.time)


class ConversationLog:
    """Keeps the last messages; merges streamed assistant chunks into one entry."""

    def __init__(self, bus: EventBus, size: int = 200) -> None:
        self.entries: deque[Entry] = deque(maxlen=size)
        self._open: Entry | None = None
        bus.subscribe(SpeechRecognized, self._on_user)
        bus.subscribe(AssistantReply, self._on_reply)

    def _on_user(self, e: SpeechRecognized) -> None:
        self._open = None
        self.entries.append(Entry("user", e.text, e.source))

    def _on_reply(self, e: AssistantReply) -> None:
        if e.text:
            if self._open is None:
                self._open = Entry("assistant", e.text)
                self.entries.append(self._open)
            else:
                self._open.text = f"{self._open.text} {e.text}".strip()
        if e.final:
            self._open = None

    def dump(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self.entries]


class LogBuffer:
    """Loguru sink keeping recent records and forwarding them to the bus."""

    def __init__(self, size: int = 500) -> None:
        self.records: deque[dict[str, Any]] = deque(maxlen=size)
        self._bus: EventBus | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sink_id: int | None = None

    def install(self, bus: EventBus, loop: asyncio.AbstractEventLoop, level: str = "INFO") -> None:
        self._bus, self._loop = bus, loop
        self._sink_id = logger.add(self._sink, level=level, format="{message}")

    def remove(self) -> None:
        if self._sink_id is not None:
            logger.remove(self._sink_id)
            self._sink_id = None

    def _sink(self, message: Any) -> None:
        record = message.record
        item = {
            "ts": record["time"].timestamp(),
            "level": record["level"].name,
            "module": record["name"] or "",
            "message": record["message"],
        }
        self.records.append(item)
        if self._bus is not None and self._loop is not None and not self._loop.is_closed():
            event = LogRecord(level=item["level"], message=item["message"], module=item["module"])
            self._bus.emit_threadsafe(event, self._loop)
