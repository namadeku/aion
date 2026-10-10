"""Dialog manager: turns user utterances into actions and replies.

Pipeline for one utterance (a *turn*):

1. ``on_utterance`` interceptors (plugin hooks) may consume the phrase;
2. the router matches a plugin command (exact templates, then fuzzy);
3. otherwise the fallback (LLM with plugin tools) answers.

A new utterance while a turn is running interrupts it (barge-in), unless the running turn
is waiting for an answer via :meth:`Turn.ask` — then the utterance *is* that answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from loguru import logger

from aion.config import ConfigStore
from aion.core.bus import EventBus
from aion.core.events import (
    AssistantReply,
    BargeIn,
    EmotionChanged,
    IntentMatched,
    Notification,
    Source,
    SpeechRecognized,
)
from aion.core.router import CommandSpec, Match, Router
from aion.core.speech import SpeechOutput
from aion.core.state import StateMachine
from aion.text import normalize

Interceptor = Callable[["Turn"], Awaitable[bool]]
Fallback = Callable[["Turn"], Awaitable[None]]

_YES = {"да", "конечно", "подтверждаю", "давай", "выполняй", "ага", "угу", "точно", "yes", "yep"}
_NO = {"нет", "не", "отмена", "отмени", "стоп", "no", "cancel"}


class SlotError(ValueError):
    pass


def convert_slots(slots: dict[str, str], types: dict[str, type]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, raw in slots.items():
        kind = types.get(name, str)
        try:
            if kind is int:
                out[name] = int(float(raw.replace(",", ".")))
            elif kind is float:
                out[name] = float(raw.replace(",", "."))
            else:
                out[name] = raw
        except ValueError as e:
            raise SlotError(f"{name}={raw!r}") from e
    return out


def parse_yes_no(text: str) -> bool | None:
    words = set(normalize(text, numbers=False).split())
    if words & _NO:
        return False
    if words & _YES:
        return True
    return None


class Turn:
    """One request/response exchange. Plugins see it through their ``ctx``."""

    def __init__(self, dialog: DialogManager, text: str, source: Source) -> None:
        self.dialog = dialog
        self.text = text
        self.source = source

    async def say(self, text: str, *, emotion: str | None = None, wait: bool = True) -> None:
        await self.dialog.say(text, emotion=emotion, wait=wait)

    async def ask(self, question: str, timeout: float | None = None) -> str | None:
        return await self.dialog.ask(question, timeout=timeout)

    async def confirm(self, question: str, timeout: float | None = None) -> bool:
        return await self.dialog.confirm(question, timeout=timeout)


class DialogManager:
    def __init__(
        self,
        bus: EventBus,
        state: StateMachine,
        speech: SpeechOutput,
        router: Router,
        config: ConfigStore,
    ) -> None:
        self.bus = bus
        self.state = state
        self.speech = speech
        self.router = router
        self.config = config
        self.interceptors: list[Interceptor] = []
        self.fallback: Fallback | None = None
        self.ask_timeout: float = 15.0
        self.follow_up_until: float = 0.0
        self.last_reply: str = ""
        self._turn: asyncio.Task[Any] | None = None
        self._answer: asyncio.Future[str] | None = None

    # -- public API ---------------------------------------------------------------------

    @property
    def awaiting_answer(self) -> bool:
        return self._answer is not None and not self._answer.done()

    @property
    def in_follow_up(self) -> bool:
        return time.monotonic() < self.follow_up_until

    @property
    def busy(self) -> bool:
        return self._turn is not None and not self._turn.done()

    @property
    def free(self) -> bool:
        """Nobody is talking and nothing is awaited: a good moment for an unprompted remark."""
        return not self.busy and not self.speech.busy and self.state.state == "idle"

    async def proactive(
        self,
        run: Callable[[Turn], Coroutine[Any, Any, bool]],
        text: str,
        *,
        follow_up: bool = False,
    ) -> bool | None:
        """Run an unprompted turn (a remark on an event) and wait for it.

        It is an ordinary turn for barge-in: a user's phrase interrupts it. Returns what
        ``run`` returned (whether something was said) or None if it was interrupted.
        """
        turn = Turn(self, text, "plugin")
        task = asyncio.create_task(run(turn), name="dialog-proactive")
        self._turn = task
        try:
            said = await task
            await self.speech.flush()
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():  # type: ignore[union-attr]
                raise
            return None
        except Exception:
            logger.exception("Ошибка реплики по событию {!r}", text)
            return False
        if said and follow_up:
            self.follow_up_until = time.monotonic() + self.config.config.assistant.follow_up_seconds
        return said

    async def submit(self, text: str, source: Source = "text") -> None:
        """Feed a user utterance. Returns immediately; the turn runs in the background."""
        text = text.strip()
        if not text:
            return
        if self._answer is not None and not self._answer.done():
            await self.bus.publish(SpeechRecognized(text=text, source=source))
            self._answer.set_result(text)
            return
        if self.busy or self.speech.busy:
            await self.interrupt("text" if source != "voice" else "speech")
        self._turn = asyncio.create_task(self._run_turn(text, source), name="dialog-turn")

    async def wait(self) -> None:
        """Wait for the current turn (if any) to finish."""
        if self._turn is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn

    async def interrupt(self, reason: str = "text") -> None:
        """Barge-in: stop speaking and cancel the running turn."""
        await self.speech.stop()
        if self._answer is not None and not self._answer.done():
            self._answer.cancel()
        if self.busy and self._turn is not asyncio.current_task():
            assert self._turn is not None
            self._turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn
        self.follow_up_until = 0.0
        self.bus.emit(BargeIn(reason=reason))  # type: ignore[arg-type]

    async def say(self, text: str, *, emotion: str | None = None, wait: bool = True) -> None:
        """Say a phrase — inside a turn or proactively (timers, reminders)."""
        if emotion:
            await self.set_emotion(emotion)
        self.last_reply = text
        await self.bus.publish(AssistantReply(text=text))
        done = self.speech.speak(text)
        if wait:
            await done

    async def say_chunk(self, text: str) -> None:
        """Queue one sentence of a streamed reply (does not wait for playback)."""
        await self.bus.publish(AssistantReply(text=text, final=False))
        self.speech.speak(text)

    async def end_reply(self, full_text: str) -> None:
        """Mark the end of a streamed reply; ``full_text`` becomes the "repeat" text."""
        if full_text:
            self.last_reply = full_text
        await self.bus.publish(AssistantReply(text="", final=True))

    async def ask(self, question: str, timeout: float | None = None) -> str | None:
        """Say a question and wait for the next utterance (no wake word needed)."""
        await self.say(question)
        loop = asyncio.get_running_loop()
        self._answer = loop.create_future()
        try:
            with self.state.active("listening"):
                return await asyncio.wait_for(self._answer, timeout or self.ask_timeout)
        except (TimeoutError, asyncio.CancelledError):
            if asyncio.current_task() and asyncio.current_task().cancelling():  # type: ignore[union-attr]
                raise
            return None
        finally:
            self._answer = None

    async def confirm(self, question: str, timeout: float | None = None) -> bool:
        for _ in range(2):
            answer = await self.ask(question, timeout)
            if answer is None:
                return False
            verdict = parse_yes_no(answer)
            if verdict is not None:
                return verdict
            question = "Простите, так да или нет?"
        return False

    async def set_emotion(self, emotion: str, intensity: float = 1.0) -> None:
        await self.bus.publish(EmotionChanged(emotion=emotion, intensity=intensity))  # type: ignore[arg-type]

    async def notify(self, title: str, text: str = "", level: str = "info") -> None:
        await self.bus.publish(Notification(title=title, text=text, level=level))  # type: ignore[arg-type]

    async def execute(self, match: Match, turn: Turn) -> bool:
        """Run a matched command. Returns False if the handler declined the phrase."""
        spec = match.command
        try:
            kwargs = convert_slots(match.slots, spec.slot_types)
        except SlotError:
            await turn.say("Не получилось разобрать число в команде. Повторите, пожалуйста.")
            return True
        if spec.dangerous and not await turn.confirm(self._confirm_question(spec)):
            await turn.say("Отменено.")
            return True
        return await spec.handler(turn, **kwargs) is not False

    # -- internals ----------------------------------------------------------------------

    def _confirm_question(self, spec: CommandSpec) -> str:
        what = spec.description or spec.name
        address = self.config.config.assistant.user_address
        return f"{address.capitalize()}, подтвердите: {what}?"

    async def _run_turn(self, text: str, source: Source) -> None:
        with self.state.active("thinking"):
            await self.bus.publish(SpeechRecognized(text=text, source=source))
            turn = Turn(self, text, source)
            try:
                await self._dispatch(turn)
                await self.speech.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка обработки фразы {!r}", text)
                await self.say("Простите, при выполнении команды произошла ошибка.")
        if source == "voice":
            self.follow_up_until = time.monotonic() + self.config.config.assistant.follow_up_seconds

    async def _dispatch(self, turn: Turn) -> None:
        for interceptor in list(self.interceptors):
            try:
                if await interceptor(turn):
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка в перехватчике on_utterance")
        for match in self.router.match_all(turn.text)[:3]:
            logger.info(
                "Команда {}.{} ({:.0f}%) slots={}",
                match.command.plugin,
                match.command.name,
                match.score,
                match.slots,
            )
            await self.bus.publish(
                IntentMatched(
                    plugin=match.command.plugin,
                    command=match.command.name,
                    pattern=match.pattern,
                    score=match.score,
                    slots=match.slots,
                )
            )
            if await self.execute(match, turn):
                return
            logger.debug("Команда {} отказалась от фразы", match.command.name)
        if self.fallback is not None:
            await self.fallback(turn)
        else:
            await turn.say("Я пока не знаю такой команды.")
