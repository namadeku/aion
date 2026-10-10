"""The conversation in the log: what the user said and what the assistant answered.

Without it, the log shows commands and timings but not the dialog itself, so a repeated
phrase or a claimed-but-not-done action can't be traced.
"""

from __future__ import annotations

from loguru import logger

from aion.core.bus import EventBus
from aion.core.events import AssistantReply, SpeechRecognized, ToolCalled


class TranscriptLog:
    def __init__(self, bus: EventBus, name: str) -> None:
        self.name = name
        self._reply: list[str] = []
        self._unsubscribe = [
            bus.subscribe(SpeechRecognized, self._on_user),
            bus.subscribe(AssistantReply, self._on_reply),
            bus.subscribe(ToolCalled, self._on_tool),
        ]

    def close(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()

    def _on_user(self, event: SpeechRecognized) -> None:
        logger.info("» Пользователь ({}): {}", event.source, event.text)

    def _on_tool(self, event: ToolCalled) -> None:
        logger.info("  инструмент {}.{}({})", event.plugin, event.tool, event.arguments)

    def _on_reply(self, event: AssistantReply) -> None:
        if event.text:
            self._reply.append(event.text)
        if event.final and self._reply:
            logger.info("« {}: {}", self.name, " ".join(self._reply))
            self._reply = []
