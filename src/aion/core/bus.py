"""Asynchronous publish/subscribe event bus.

Handlers may be sync or async. A failing handler is logged and never breaks the publisher
or other subscribers.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, overload

from loguru import logger

from aion.core.events import Event

type Handler[E: Event] = Callable[[E], Awaitable[None] | None]
Unsubscribe = Callable[[], None]

ANY = "*"


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler[Any]]] = defaultdict(list)
        self._background: set[asyncio.Task[None]] = set()

    @overload
    def subscribe[E: Event](self, event: type[E], handler: Handler[E]) -> Unsubscribe: ...
    @overload
    def subscribe(self, event: str, handler: Handler[Event]) -> Unsubscribe: ...

    def subscribe(self, event: type[Event] | str, handler: Handler[Any]) -> Unsubscribe:
        """Subscribe to an event class, its ``type`` string, or ``"*"`` for everything."""
        key = event if isinstance(event, str) else event.type
        self._handlers[key].append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers[key]:
                self._handlers[key].remove(handler)

        return unsubscribe

    async def publish(self, event: Event) -> None:
        """Deliver ``event`` to all subscribers and wait for them."""
        handlers = [*self._handlers.get(event.type, ()), *self._handlers.get(ANY, ())]
        if not handlers:
            return
        await asyncio.gather(*(self._call(h, event) for h in handlers))

    def emit(self, event: Event) -> None:
        """Fire-and-forget publish; safe to call from sync code inside the event loop."""
        task = asyncio.get_running_loop().create_task(self.publish(event))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def emit_threadsafe(self, event: Event, loop: asyncio.AbstractEventLoop) -> None:
        """Publish from a non-asyncio thread (audio callbacks, tray, hotkeys)."""
        loop.call_soon_threadsafe(self.emit, event)

    @staticmethod
    async def _call(handler: Handler[Any], event: Event) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в обработчике события {}", event.type)

    async def drain(self) -> None:
        """Wait for fire-and-forget deliveries (used in tests and on shutdown)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)
