"""Control Aion from Telegram through the user's own bot (long polling, no webhooks).

Text from whitelisted chats becomes a dialog turn (``source="text"``); the assistant's replies
of that turn go back to the chat. Confirmations of dangerous steps arrive as inline buttons
Да/Нет and are answered through the same mechanism as the buttons of the web UI
(``CommandEngine.answer``); for the plain spoken ``ctx.confirm`` question the buttons feed
"да"/"нет" into the dialog as the user's answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from aion.core.events import (
    AssistantReply,
    ConfirmRequested,
    RunFinished,
    RunStarted,
    StepFinished,
)
from aion.sdk import Plugin

from .api import MESSAGE_LIMIT, BotApi, TelegramError, split_message

BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
FATAL_BACKOFF = 300.0  # the token is rejected: check again rarely
RATE_WINDOW = 60.0
PENDING_TTL = 15.0  # how long a submitted text waits for its RunStarted event
QUICK_ANSWER_WAIT = 30.0  # how long to wait for a spoken question to start awaiting an answer

HELP = (
    "Пишите текстом то, что сказали бы ассистенту.\n"
    "/stop - прервать текущее действие и замолчать\n"
    "/help - эта справка\n"
    "Опасные действия я подтверждаю кнопками Да/Нет."
)


def parse_chat_ids(raw: object) -> set[int]:
    """``"123, -456; 789"`` -> ``{123, -456, 789}``; junk is ignored."""
    return {int(m) for m in re.findall(r"-?\d+", str(raw or ""))}


@dataclass
class _Job:
    """One outgoing Bot API call; ``done`` receives its result (or None on failure)."""

    method: str
    params: dict[str, Any]
    done: Callable[[Any], None] | None = None


@dataclass
class _Confirm:
    chat_id: int
    question: str
    message_id: int | None = None


@dataclass
class _ChatState:
    stamps: deque[float] = field(default_factory=deque)
    warned: bool = False
    hinted_at: float = 0.0


class Telegram(Plugin):
    transport: httpx.AsyncBaseTransport | None = None  # tests inject a mock transport

    async def on_load(self) -> None:
        self._api: BotApi | None = None
        self._poller: asyncio.Task[None] | None = None
        self._sender: asyncio.Task[None] | None = None
        self._outbox: asyncio.Queue[_Job] = asyncio.Queue()
        self._chats: dict[int, _ChatState] = {}
        self._pending: deque[tuple[float, int, str]] = deque()  # (ts, chat_id, text)
        self._run: tuple[str, int] | None = None  # run id and chat of the running Telegram turn
        self._buffer: list[str] = []
        self._last_reply = ""
        self._confirms: dict[tuple[str, str], _Confirm] = {}
        self._unsubscribe: list[Callable[[], None]] = []
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._offer_tasks: set[asyncio.Task[None]] = set()
        bus = self.host.bus
        self._unsubscribe = [
            bus.subscribe(RunStarted, self._on_run_started),
            bus.subscribe(AssistantReply, self._on_reply),
            bus.subscribe(ConfirmRequested, self._on_confirm_requested),
            bus.subscribe(StepFinished, self._on_step_finished),
            bus.subscribe(RunFinished, self._on_run_finished),
        ]
        await self._start()

    async def on_unload(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        await self._stop()

    async def on_settings_changed(self) -> None:
        await self._stop()
        await self._start()

    # -- start / stop ---------------------------------------------------------------------

    @property
    def allowed(self) -> set[int]:
        return parse_chat_ids(self.config["allowed_chat_ids"])

    async def _start(self) -> None:
        token = str(self.config["token"] or "").strip()
        if not self.config["enabled"]:
            return
        if not token:
            self.log.warning("Telegram включён, но токен не задан (AION_TELEGRAM_TOKEN)")
            return
        # httpx logs every request URL at INFO and the URL contains the token
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._api = BotApi(token, self.transport)
        self._sender = asyncio.create_task(self._send_loop(), name="telegram-send")
        self._poller = asyncio.create_task(self._poll_loop(), name="telegram-poll")
        if not self.allowed:
            self.log.warning(
                "Белый список Telegram пуст: бот не отвечает никому. Напишите боту /start, "
                "он подскажет chat id; добавьте его в настройки плагина."
            )

    async def _stop(self) -> None:
        tasks = [t for t in (self._poller, self._sender, *self._offer_tasks) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._poller = self._sender = None
        self._offer_tasks.clear()
        self._outbox = asyncio.Queue()
        self._confirms.clear()
        self._run = None
        self._buffer.clear()
        if self._api is not None:
            await self._api.close()
            self._api = None

    async def drain(self) -> None:
        """Wait until everything queued for sending has been sent (tests, shutdown)."""
        await self._outbox.join()

    # -- polling ----------------------------------------------------------------------------

    def _fail(self, e: TelegramError) -> None:
        self.log.warning("Telegram: {}", e)

    async def _poll_loop(self) -> None:
        api = self._api
        assert api is not None
        backoff = BACKOFF_START
        offset: int | None = None
        while True:
            try:
                if offset is None:
                    offset = await self._skip_backlog(api)
                    me = await api.call("getMe")
                    self.log.info("Telegram: бот @{} подключён", (me or {}).get("username", "?"))
                updates = await api.get_updates(offset)
                backoff = BACKOFF_START
            except asyncio.CancelledError:
                raise
            except TelegramError as e:
                self._fail(e)
                delay = e.retry_after or (FATAL_BACKOFF if e.fatal else backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                await self._sleep(delay)
                continue
            except Exception as e:
                self.log.error("Telegram: неожиданная ошибка опроса: {}", api.redact(repr(e)))
                await self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            for update in updates:
                offset = max(offset or 0, int(update.get("update_id", 0)) + 1)
                try:
                    await self.handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.log.error("Telegram: ошибка обработки сообщения: {}", api.redact(repr(e)))

    @staticmethod
    async def _skip_backlog(api: BotApi) -> int:
        """Start after the newest update: messages sent while Aion was off must not run now."""
        last = await api.get_updates(-1, timeout=0)
        return int(last[-1]["update_id"]) + 1 if last else 0

    # -- incoming ---------------------------------------------------------------------------

    async def handle_update(self, update: dict[str, Any]) -> None:
        if callback := update.get("callback_query"):
            await self._handle_callback(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        chat_id = (message.get("chat") or {}).get("id")
        if not isinstance(text, str) or not isinstance(chat_id, int):
            return
        text = text.strip()
        command = self._command(text)
        if chat_id not in self.allowed:
            await self._unknown_chat(chat_id, command)
            return
        if not self._within_rate(chat_id):
            return
        match command:
            case "/start" | "/help":
                await self._send(chat_id, HELP)
            case "/stop":
                await self.host.dialog.interrupt("hotkey")
                await self._send(chat_id, self.g("Остановил.", "Остановила."))
            case None:
                await self._text(chat_id, text)
            case _:
                await self._send(chat_id, "Не знаю такую команду. /help")

    @staticmethod
    def _command(text: str) -> str | None:
        if not text.startswith("/"):
            return None
        return text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()

    async def _unknown_chat(self, chat_id: int, command: str | None) -> None:
        state = self._chats.setdefault(chat_id, _ChatState())
        if command != "/start":
            return  # silence: do not even confirm that the bot exists
        now = time.monotonic()
        if now - state.hinted_at < RATE_WINDOW:
            return
        state.hinted_at = now
        self.log.info("Telegram: /start из чата {}, которого нет в белом списке", chat_id)
        await self._send(chat_id, f"chat id: {chat_id}, add it to the whitelist")

    def _within_rate(self, chat_id: int) -> bool:
        state = self._chats.setdefault(chat_id, _ChatState())
        now = time.monotonic()
        while state.stamps and now - state.stamps[0] > RATE_WINDOW:
            state.stamps.popleft()
        if len(state.stamps) >= int(self.config["rate_limit_per_minute"]):
            if not state.warned:
                state.warned = True
                self.create_task(self._send(chat_id, "Слишком часто, подождите минуту."))
            return False
        state.warned = False
        state.stamps.append(now)
        return True

    async def _text(self, chat_id: int, text: str) -> None:
        limit = int(self.config["max_input_chars"])
        if len(text) > limit:
            await self._send(chat_id, f"Слишком длинное сообщение (не больше {limit} символов).")
            return
        dialog = self.host.dialog
        if not dialog.awaiting_answer:  # otherwise the text is just the answer to a question
            now = time.monotonic()
            while self._pending and now - self._pending[0][0] > PENDING_TTL:
                self._pending.popleft()
            self._pending.append((now, chat_id, text))
        await dialog.submit(text, source="text")

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
        query_id = callback.get("id")
        data = str(callback.get("data") or "")
        if not isinstance(chat_id, int) or chat_id not in self.allowed:
            return
        toast = "Не понял"
        parts = data.split(":")
        if len(parts) == 4 and parts[0] == "c":
            _, run_id, step_id, verdict = parts
            confirm = self._confirms.get((run_id, step_id))
            if confirm is None or confirm.chat_id != chat_id:
                toast = "Время вышло"
            elif self.host.commands.answer(run_id, step_id, verdict == "1"):
                toast = "Принято"
            else:
                toast = "Время вышло"
        elif len(parts) == 2 and parts[0] == "a":
            if self.host.dialog.awaiting_answer:
                await self.host.dialog.submit("да" if parts[1] == "1" else "нет", source="text")
                toast = "Принято"
            else:
                toast = "Вопрос уже закрыт"
        if query_id is not None:
            self._enqueue("answerCallbackQuery", callback_query_id=query_id, text=toast)

    # -- events from the assistant ----------------------------------------------------------

    def _on_run_started(self, event: RunStarted) -> None:
        now = time.monotonic()
        while self._pending and now - self._pending[0][0] > PENDING_TTL:
            self._pending.popleft()
        for i, (_, chat_id, text) in enumerate(self._pending):
            if text == event.title and event.source == "text":
                del self._pending[i]
                self._run = (event.run_id, chat_id)
                self._buffer.clear()
                self._enqueue("sendChatAction", chat_id=chat_id, action="typing")
                return

    def _on_reply(self, event: AssistantReply) -> None:
        if self._run is None:
            return
        if not event.final:
            self._buffer.append(event.text)
            return
        text = " ".join([*self._buffer, event.text]).strip() if self._buffer else event.text
        self._buffer.clear()
        if text.strip() and not any(c.question == text for c in self._confirms.values()):
            self._last_reply = text
            self._enqueue_text(self._run[1], text)
            if text.rstrip().endswith("?"):
                self._offer_quick_answer(self._run[0], self._run[1])

    def _on_confirm_requested(self, event: ConfirmRequested) -> None:
        if self._run is None or self._run[0] != event.run_id:
            return
        chat_id = self._run[1]
        key = (event.run_id, event.step_id)
        confirm = _Confirm(chat_id, event.question)
        self._confirms[key] = confirm
        keyboard = [
            [
                {"text": "Да", "callback_data": f"c:{event.run_id}:{event.step_id}:1"},
                {"text": "Нет", "callback_data": f"c:{event.run_id}:{event.step_id}:0"},
            ]
        ]

        def remember(result: Any) -> None:
            if isinstance(result, dict):
                confirm.message_id = result.get("message_id")

        self._enqueue(
            "sendMessage",
            done=remember,
            chat_id=chat_id,
            text="Подтвердите:" if self._last_reply == event.question else event.question,
            reply_markup={"inline_keyboard": keyboard},
        )

    def _on_step_finished(self, event: StepFinished) -> None:
        confirm = self._confirms.pop((event.run_id, event.step_id), None)
        if confirm is None:
            return
        verdict = event.detail or event.status
        self._enqueue(
            "sendMessage",
            chat_id=confirm.chat_id,
            text=f"{confirm.question}\n-> {verdict}",
        )
        if confirm.message_id is not None:  # drop the stale buttons
            self._enqueue(
                "editMessageReplyMarkup",
                chat_id=confirm.chat_id,
                message_id=confirm.message_id,
                reply_markup={"inline_keyboard": []},
            )

    def _on_run_finished(self, event: RunFinished) -> None:
        if self._run is None or self._run[0] != event.run_id:
            return
        chat_id = self._run[1]
        if self._buffer:
            self._enqueue_text(chat_id, " ".join(self._buffer))
            self._buffer.clear()
        if event.status == "error":
            self._enqueue_text(chat_id, "Не получилось выполнить.")
        self._run = None
        for key in [k for k in self._confirms if k[0] == event.run_id]:
            del self._confirms[key]

    def _offer_quick_answer(self, run_id: str, chat_id: int) -> None:
        """A spoken question ended with "?": offer Да/Нет once the dialog awaits the answer."""

        async def offer() -> None:
            deadline = time.monotonic() + QUICK_ANSWER_WAIT
            while time.monotonic() < deadline:
                if self._run is None or self._run[0] != run_id:
                    return
                if self.host.dialog.awaiting_answer:
                    break
                await self._sleep(0.2)
            else:
                return
            if any(k[0] == run_id for k in self._confirms):
                return  # the step already has its own buttons
            self._enqueue(
                "sendMessage",
                chat_id=chat_id,
                text="Ответить:",
                reply_markup={
                    "inline_keyboard": [
                        [
                            {"text": "Да", "callback_data": "a:1"},
                            {"text": "Нет", "callback_data": "a:0"},
                        ]
                    ]
                },
            )

        task = asyncio.get_running_loop().create_task(offer(), name="telegram-offer")
        self._offer_tasks.add(task)
        task.add_done_callback(self._offer_tasks.discard)

    # -- outgoing ---------------------------------------------------------------------------

    def _enqueue(
        self, method: str, *, done: Callable[[Any], None] | None = None, **params: Any
    ) -> None:
        if self._api is not None:
            self._outbox.put_nowait(_Job(method, params, done))

    def _enqueue_text(self, chat_id: int, text: str) -> None:
        for part in split_message(text, MESSAGE_LIMIT):
            self._enqueue("sendMessage", chat_id=chat_id, text=part)

    async def _send(self, chat_id: int, text: str) -> None:
        self._enqueue_text(chat_id, text)

    async def _send_loop(self) -> None:
        api = self._api
        assert api is not None
        while True:
            job = await self._outbox.get()
            result: Any = None
            try:
                for attempt in (1, 2):
                    try:
                        result = await api.call(job.method, **job.params)
                        break
                    except TelegramError as e:
                        if attempt == 1 and e.retry_after:
                            await self._sleep(min(e.retry_after, 30))
                            continue
                        self.log.warning("Telegram: не удалось отправить ({})", e)
                        break
                if job.done is not None:
                    with contextlib.suppress(Exception):
                        job.done(result)
            finally:
                self._outbox.task_done()
