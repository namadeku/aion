"""Speech output: a queue of phrases rendered one by one, interruptible at any moment.

Concrete outputs only implement :meth:`SpeechOutput.render` (print to console, or synthesize
and play audio). The base class handles queueing, ``tts_*`` events, the ``speaking`` state
flag and barge-in.
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from loguru import logger
from rich.console import Console

from aion.core.bus import EventBus
from aion.core.events import TtsFinished, TtsStarted
from aion.core.state import StateMachine


@dataclass
class _Item:
    text: str
    done: asyncio.Future[bool] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class SpeechOutput(ABC):
    def __init__(self, bus: EventBus, state: StateMachine) -> None:
        self._bus = bus
        self._state = state
        self._queue: asyncio.Queue[_Item] = asyncio.Queue()
        self._pending: list[_Item] = []
        self._worker: asyncio.Task[None] | None = None
        self._current: asyncio.Task[None] | None = None
        self._speaking = False

    @abstractmethod
    async def render(self, text: str) -> None:
        """Produce the phrase (blocking until it has been fully spoken). Must be cancellable."""

    async def prepare(self, text: str) -> None:  # noqa: B027 - optional hook
        """Optional look-ahead hook: called when a phrase is queued (e.g. to pre-synthesize)."""

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="speech-output")

    async def close(self) -> None:
        await self.stop()
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    def speak(self, text: str) -> asyncio.Future[bool]:
        """Queue a phrase; the future resolves to True when spoken, False if interrupted."""
        item = _Item(text.strip())
        if not item.text:
            item.done.set_result(True)
            return item.done
        self._pending.append(item)
        self._queue.put_nowait(item)
        asyncio.get_running_loop().create_task(self._safe_prepare(item.text))
        return item.done

    async def say(self, text: str) -> bool:
        return await self.speak(text)

    async def flush(self) -> None:
        """Wait until everything queued so far has been spoken (or dropped)."""
        if self._pending:
            await asyncio.gather(*(i.done for i in list(self._pending)), return_exceptions=True)

    async def stop(self) -> None:
        """Barge-in: drop the queue and cut the current phrase."""
        while not self._queue.empty():
            item = self._queue.get_nowait()
            self._finish(item, spoken=False)
        if self._current and not self._current.done():
            self._current.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._current

    async def _safe_prepare(self, text: str) -> None:
        try:
            await self.prepare(text)
        except Exception:
            logger.exception("Ошибка подготовки фразы")

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item.done.done():
                continue
            if not self._speaking:
                self._speaking = True
                self._state.raise_flag("speaking")
            await self._bus.publish(TtsStarted(text=item.text))
            self._current = asyncio.create_task(self.render(item.text))
            interrupted = False
            try:
                await self._current
            except asyncio.CancelledError:
                interrupted = True
                if self._worker is not None and self._worker.cancelling():
                    self._finish(item, spoken=False)
                    raise
            except Exception:
                interrupted = True
                logger.exception("Ошибка воспроизведения речи")
            finally:
                self._current = None
            await self._bus.publish(TtsFinished(text=item.text, interrupted=interrupted))
            self._finish(item, spoken=not interrupted)

    def _finish(self, item: _Item, *, spoken: bool) -> None:
        if not item.done.done():
            item.done.set_result(spoken)
        if item in self._pending:
            self._pending.remove(item)
        if not self._pending and self._speaking:
            self._speaking = False
            self._state.lower_flag("speaking")


class ConsoleOutput(SpeechOutput):
    """Text-mode output: prints replies instead of speaking them."""

    def __init__(
        self, bus: EventBus, state: StateMachine, name: str = "Aion", console: Console | None = None
    ) -> None:
        super().__init__(bus, state)
        self.name = name
        self.console = console or Console(highlight=False)

    async def render(self, text: str) -> None:
        self.console.print(f"[bold cyan]{self.name}:[/] {text}")


class NullOutput(SpeechOutput):
    """Collects phrases without output — for tests and headless use."""

    def __init__(self, bus: EventBus, state: StateMachine) -> None:
        super().__init__(bus, state)
        self.spoken: list[str] = []

    async def render(self, text: str) -> None:
        self.spoken.append(text)
