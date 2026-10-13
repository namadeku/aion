"""Telegram plugin: whitelist, turns, replies, confirmation buttons, /stop, token hygiene.

No network: the Bot API is a ``httpx.MockTransport``; the dialog and the command engine are real.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from loguru import logger

from aion.app import Aion
from aion.core.events import ConfirmRequested, Event
from aion.core.speech import NullOutput
from tests.helpers import load_builtin_module, settle
from tests.test_commands_engine import FakeBackend, add, asking, wait_for

TOKEN = "123456:SECRET-token-value"
CHAT = 4242


class FakeBot:
    """A stand-in for api.telegram.org: records calls, serves scripted ``getUpdates``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue()
        self.failures: list[httpx.Response | Exception] = []
        self.urls: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        method = request.url.path.rsplit("/", 1)[-1]
        params = json.loads(request.content or b"{}")
        self.calls.append((method, params))
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        if method == "getUpdates":
            if params.get("offset") == -1:
                return _ok([])
            return _ok(await self.updates.get())
        if method == "getMe":
            return _ok({"username": "aion_test_bot"})
        if method == "sendMessage":
            return _ok({"message_id": len(self.calls)})
        return _ok(True)

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def texts(self) -> list[str]:
        return [p["text"] for p in self.sent()]


def _ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def _plugin(app: Aion) -> Any:
    plugin = app.plugins.instance("telegram")
    assert plugin is not None
    return plugin


def message(text: str, chat: int = CHAT, update_id: int = 1) -> dict[str, Any]:
    return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}


def button(data: str, chat: int = CHAT) -> dict[str, Any]:
    return {
        "update_id": 99,
        "callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": chat}}},
    }


@pytest.fixture
def bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def events(app: Aion) -> list[Event]:
    seen: list[Event] = []
    app.bus.subscribe("*", seen.append)
    return seen


async def start_plugin(app: Aion, bot: FakeBot, allowed: str = str(CHAT), **extra: Any) -> Any:
    plugin = _plugin(app)
    plugin.transport = httpx.MockTransport(bot)
    settings = {"enabled": True, "token": TOKEN, "allowed_chat_ids": allowed, **extra}
    app.store.update({"plugins": {"settings": {"telegram": settings}}})
    await plugin.on_settings_changed()
    return plugin


@pytest.fixture
async def tg(app: Aion, bot: FakeBot) -> AsyncIterator[Any]:
    plugin = await start_plugin(app, bot)
    yield plugin
    await plugin.on_unload()


async def feed(plugin: Any, update: dict[str, Any], app: Aion | None = None) -> None:
    await plugin.handle_update(update)
    if app is not None:
        await settle(app)
        await asyncio.sleep(0.01)  # bus events published by fire-and-forget tasks
    await plugin.drain()


# -- settings and lifecycle -----------------------------------------------------------------


async def test_disabled_does_nothing(app: Aion, bot: FakeBot) -> None:
    plugin = _plugin(app)
    plugin.transport = httpx.MockTransport(bot)
    await plugin.on_settings_changed()
    await asyncio.sleep(0.05)
    assert bot.calls == []


async def test_no_token_does_nothing(app: Aion, bot: FakeBot) -> None:
    plugin = _plugin(app)
    plugin.transport = httpx.MockTransport(bot)
    app.store.update({"plugins": {"settings": {"telegram": {"enabled": True, "token": ""}}}})
    await plugin.on_settings_changed()
    await asyncio.sleep(0.05)
    assert bot.calls == []


async def test_backlog_is_skipped_on_start(app: Aion, bot: FakeBot) -> None:
    async def with_backlog(request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)
        if request.url.path.endswith("getUpdates") and params.get("offset") == -1:
            return _ok([{**message("старая команда"), "update_id": 500}])
        return await bot(request)

    plugin = _plugin(app)
    plugin.transport = httpx.MockTransport(with_backlog)
    app.store.update(
        {
            "plugins": {
                "settings": {
                    "telegram": {"enabled": True, "token": TOKEN, "allowed_chat_ids": "4242"}
                }
            }
        }
    )
    await plugin.on_settings_changed()
    for _ in range(100):
        if any(p.get("offset") == 501 for _, p in bot.calls):
            break
        await asyncio.sleep(0.01)
    assert any(p.get("offset") == 501 for m, p in bot.calls if m == "getUpdates"), bot.calls
    assert app.dialog.last_reply == ""
    await plugin.on_unload()


async def test_poll_loop_delivers_updates(app: Aion, bot: FakeBot, tg: Any) -> None:
    app.dialog.interceptors.append(_echo(app))
    await bot.updates.put([message("привет", update_id=7)])
    for _ in range(200):
        await asyncio.sleep(0.01)
        await tg.drain()
        if bot.texts():
            break
    assert bot.texts() == ["эхо: привет"]
    # the next request acknowledges the update
    await bot.updates.put([])
    await asyncio.sleep(0.05)
    assert any(p.get("offset") == 8 for m, p in bot.calls if m == "getUpdates")


async def test_network_errors_back_off_and_recover(app: Aion, bot: FakeBot) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        await asyncio.sleep(0)

    plugin = _plugin(app)
    bot.failures = [
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        httpx.Response(502, text="bad gateway"),
    ]
    await start_plugin(app, bot)
    # restart with a fast sleep: the first calls fail, then the loop reaches long polling
    await plugin._stop()
    plugin._sleep = fake_sleep
    bot.failures = [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")]
    bot.calls.clear()
    await plugin._start()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(m == "getMe" for m, _ in bot.calls):
            break
    assert any(m == "getMe" for m, _ in bot.calls)
    assert sleeps[:2] == [1.0, 2.0]
    await plugin.on_unload()


# -- whitelist ------------------------------------------------------------------------------


async def test_unknown_chat_is_ignored_except_start(app: Aion, bot: FakeBot, tg: Any) -> None:
    submitted: list[str] = []
    app.dialog.interceptors.append(_collect(submitted))
    await feed(tg, message("выключи компьютер", chat=555), app)
    await feed(tg, message("/stop", chat=555), app)
    await feed(tg, message("/help", chat=555), app)
    assert bot.sent() == []
    assert submitted == []

    await feed(tg, message("/start", chat=555), app)
    assert bot.texts() == ["chat id: 555, add it to the whitelist"]
    await feed(tg, message("/start@aion_bot", chat=555), app)  # rate limited hint
    assert len(bot.texts()) == 1


async def test_empty_whitelist_ignores_everyone(app: Aion, bot: FakeBot) -> None:
    plugin = await start_plugin(app, bot, allowed="")
    submitted: list[str] = []
    app.dialog.interceptors.append(_collect(submitted))
    await feed(plugin, message("привет"), app)
    assert submitted == []
    assert bot.sent() == []
    await feed(plugin, message("/start"), app)
    assert bot.texts() == [f"chat id: {CHAT}, add it to the whitelist"]
    await plugin.on_unload()


async def test_unknown_chat_buttons_are_ignored(app: Aion, bot: FakeBot, tg: Any) -> None:
    await feed(tg, button("a:1", chat=555), app)
    assert bot.sent("answerCallbackQuery") == []


async def test_group_ids_and_junk_in_whitelist(app: Aion, bot: FakeBot) -> None:
    plugin = await start_plugin(app, bot, allowed="12, -1001234; мусор 77")
    assert plugin.allowed == {12, -1001234, 77}
    await plugin.on_unload()


# -- turns and replies ----------------------------------------------------------------------


def _echo(app: Aion):
    async def interceptor(turn: Any) -> bool:
        await turn.say(f"эхо: {turn.text}")
        return True

    return interceptor


def _collect(into: list[str]):
    async def interceptor(turn: Any) -> bool:
        into.append(turn.text)
        return True

    return interceptor


async def test_text_becomes_a_turn_and_reply_is_sent_back(
    app: Aion, bot: FakeBot, tg: Any, events: list[Event]
) -> None:
    app.dialog.interceptors.append(_echo(app))
    await feed(tg, message("который час"), app)
    assert bot.texts() == ["эхо: который час"]
    assert bot.sent()[0]["chat_id"] == CHAT
    assert bot.sent("sendChatAction")[0]["action"] == "typing"
    assert isinstance(app.speech, NullOutput)
    assert app.speech.spoken[-1] == "эхо: который час"  # still spoken at the PC


async def test_replies_of_other_sources_are_not_forwarded(app: Aion, bot: FakeBot, tg: Any) -> None:
    app.dialog.interceptors.append(_echo(app))
    await app.dialog.submit("с клавиатуры", source="ui")
    await settle(app)
    await tg.drain()
    assert bot.sent() == []


async def test_streamed_reply_is_joined(app: Aion, bot: FakeBot, tg: Any) -> None:
    async def streaming(turn: Any) -> bool:
        await turn.dialog.say_chunk("Первая часть.")
        await turn.dialog.say_chunk("Вторая часть.")
        await turn.dialog.end_reply("Первая часть. Вторая часть.")
        return True

    app.dialog.interceptors.append(streaming)
    await feed(tg, message("расскажи"), app)
    assert bot.texts() == ["Первая часть. Вторая часть."]


async def test_long_reply_is_split(app: Aion, bot: FakeBot, tg: Any) -> None:
    async def long(turn: Any) -> bool:
        await turn.say(("слово " * 1500).strip())
        return True

    app.dialog.interceptors.append(long)
    await feed(tg, message("много"), app)
    parts = bot.texts()
    assert len(parts) == 3
    assert all(len(p) <= 4096 for p in parts)


async def test_too_long_input_rejected(app: Aion, bot: FakeBot, tg: Any) -> None:
    submitted: list[str] = []
    app.dialog.interceptors.append(_collect(submitted))
    await feed(tg, message("а" * 1001), app)
    assert submitted == []
    assert "Слишком длинное" in bot.texts()[0]


async def test_rate_limit(app: Aion, bot: FakeBot) -> None:
    plugin = await start_plugin(app, bot, rate_limit_per_minute=3)
    submitted: list[str] = []
    app.dialog.interceptors.append(_collect(submitted))
    for i in range(6):
        await feed(plugin, message(f"команда {i}"), app)
    assert submitted == ["команда 0", "команда 1", "команда 2"]
    assert [t for t in bot.texts() if "Слишком часто" in t] == ["Слишком часто, подождите минуту."]
    await plugin.on_unload()


async def test_stop_interrupts_the_running_turn(app: Aion, bot: FakeBot, tg: Any) -> None:
    started = asyncio.Event()

    async def hang(turn: Any) -> bool:
        started.set()
        await asyncio.sleep(60)
        return True

    app.dialog.interceptors.append(hang)
    await tg.handle_update(message("долго"))
    await asyncio.wait_for(started.wait(), 2)
    assert app.dialog.busy
    await feed(tg, message("/stop", update_id=2))
    assert not app.dialog.busy
    assert bot.texts()[-1] in {"Остановил.", "Остановила."}


async def test_help_and_unknown_command(app: Aion, bot: FakeBot, tg: Any) -> None:
    await feed(tg, message("/help"), app)
    await feed(tg, message("/start"), app)
    await feed(tg, message("/wat"), app)
    assert "/stop" in bot.texts()[0]
    assert bot.texts()[1] == bot.texts()[0]
    assert "Не знаю" in bot.texts()[2]


# -- confirmations --------------------------------------------------------------------------


async def run_dangerous(app: Aion, tg: Any, events: list[Event]) -> ConfirmRequested:
    add(
        app,
        "del",
        name="Удаление",
        triggers=["удали мусор"],
        actions=[{"type": "shell", "command": "x"}],
    )
    await tg.handle_update(message("удали мусор"))
    asked = await wait_for(events, ConfirmRequested)
    await asking(app)
    await asyncio.sleep(0.02)
    await tg.drain()
    return asked


def keyboard(bot: FakeBot) -> list[dict[str, str]]:
    with_markup = [
        p for p in bot.sent() if "reply_markup" in p and p["reply_markup"]["inline_keyboard"]
    ]
    return with_markup[0]["reply_markup"]["inline_keyboard"][0]


async def test_confirm_buttons_yes(app: Aion, bot: FakeBot, tg: Any, events: list[Event]) -> None:
    backend = FakeBackend()
    app.commands.backend = backend
    asked = await run_dangerous(app, tg, events)
    row = keyboard(bot)
    assert [b["text"] for b in row] == ["Да", "Нет"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in row)
    assert sum(asked.question in t for t in bot.texts()[:2]) == 1  # the question is not repeated

    await feed(tg, button(row[0]["callback_data"]), app)
    assert backend.kinds() == ["shell"]
    assert bot.sent("answerCallbackQuery")[0]["text"] == "Принято"
    assert bot.sent("editMessageReplyMarkup")  # the buttons are removed
    assert any(t.endswith("-> Да") for t in bot.texts())
    assert not app.dialog.awaiting_answer


async def test_confirm_buttons_no(app: Aion, bot: FakeBot, tg: Any, events: list[Event]) -> None:
    backend = FakeBackend()
    app.commands.backend = backend
    await run_dangerous(app, tg, events)
    row = keyboard(bot)
    await feed(tg, button(row[1]["callback_data"]), app)
    assert backend.calls == []
    assert any(t.endswith("-> Нет") for t in bot.texts())


async def test_confirm_timeout_updates_the_message(
    app: Aion, bot: FakeBot, tg: Any, events: list[Event]
) -> None:
    app.store.update({"commands": {"confirm_timeout": 3}})
    backend = FakeBackend()
    app.commands.backend = backend
    await run_dangerous(app, tg, events)
    row = keyboard(bot)
    await settle(app)
    await asyncio.sleep(0.05)
    await tg.drain()
    assert backend.calls == []
    assert any(t.endswith(("-> Нет ответа", "-> Нет")) for t in bot.texts()), bot.texts()
    assert bot.sent("editMessageReplyMarkup")
    # a late press is answered politely and does nothing
    await feed(tg, button(row[0]["callback_data"]), app)
    assert bot.sent("answerCallbackQuery")[-1]["text"] == "Время вышло"
    assert backend.calls == []


async def test_confirm_button_from_another_chat_is_refused(
    app: Aion, bot: FakeBot, tg: Any, events: list[Event]
) -> None:
    backend = FakeBackend()
    app.commands.backend = backend
    await run_dangerous(app, tg, events)
    row = keyboard(bot)
    tg_allowed = tg.allowed
    app.store.update({"plugins": {"settings": {"telegram": {"allowed_chat_ids": f"{CHAT}, 777"}}}})
    assert tg.allowed == tg_allowed | {777}
    await feed(tg, button(row[0]["callback_data"], chat=777), app)
    assert backend.calls == []
    assert bot.sent("answerCallbackQuery")[-1]["text"] == "Время вышло"
    # the real chat can still answer
    await feed(tg, button(row[1]["callback_data"]), app)


async def test_spoken_question_gets_quick_answer_buttons(app: Aion, bot: FakeBot, tg: Any) -> None:
    result: list[bool] = []

    async def asks(turn: Any) -> bool:
        result.append(await turn.confirm("Сэр, удалить всё?"))
        return True

    app.dialog.interceptors.append(asks)
    await tg.handle_update(message("сотри всё"))
    await asking(app)
    for _ in range(100):
        await asyncio.sleep(0.01)
        await tg.drain()
        if any("reply_markup" in p for p in bot.sent()):
            break
    assert bot.texts()[0] == "Сэр, удалить всё?"
    row = keyboard(bot)
    await feed(tg, button(row[0]["callback_data"]), app)
    assert result == [True]


# -- the token never leaks ------------------------------------------------------------------


async def test_token_is_never_logged(app: Aion, bot: FakeBot) -> None:
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="TRACE")
    try:
        bot.failures = [
            httpx.Response(
                401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}
            ),
        ]
        plugin = _plugin(app)

        async def fast(_: float) -> None:
            await asyncio.sleep(0.001)

        plugin._sleep = fast
        await start_plugin(app, bot)
        plugin._sleep = fast
        await asyncio.sleep(0.05)
        bot.failures = [
            httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/getMe")
        ]
        await asyncio.sleep(0.05)
        await plugin.on_unload()
    finally:
        logger.remove(sink)
    assert any("Telegram" in line for line in lines)
    assert all(TOKEN not in line for line in lines)
    assert all(TOKEN.split(":")[1] not in line for line in lines)


async def test_api_errors_are_redacted() -> None:
    module = load_builtin_module("telegram", "api")
    BotApi, TelegramError = module.BotApi, module.TelegramError

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed for {request.url}")

    api = BotApi(TOKEN, httpx.MockTransport(boom))
    with pytest.raises(TelegramError) as info:
        await api.call("getMe")
    assert TOKEN not in str(info.value)
    await api.close()
