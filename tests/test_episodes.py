from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from aion.app import Aion
from aion.core.events import AssistantReply, SpeechRecognized
from aion.llm.base import Message, StreamEvent, ToolDef
from aion.llm.brain import Brain
from aion.llm.episodes import EpisodeStore, parse_summary, say_date
from tests.helpers import load_builtin_module
from tests.test_llm import FakeProvider, text_reply

TODAY = date(2026, 10, 7)


# -- parsing and dates --------------------------------------------------------------------


def test_parse_summary_json_with_noise() -> None:
    raw = (
        "Вот запись:\n```json\n"
        '{"summary": "Говорили о работе.", "follow_ups": [{"topic": "собеседование", '
        '"when": "в пятницу"}, {"topic": "", "when": null}, {"topic": "ремонт", '
        '"when": "null"}], "closed": [3, "4", "x"]}\n```'
    )
    summary = parse_summary(raw)
    assert summary is not None
    assert summary.text == "Говорили о работе."
    assert summary.follow_ups == [("собеседование", "в пятницу"), ("ремонт", None)]
    assert summary.closed == [3, 4]


def test_parse_summary_fallbacks() -> None:
    plain = parse_summary("Болтали о погоде.")
    assert plain is not None
    assert plain.text == "Болтали о погоде."
    assert parse_summary('{"summary": ""}') is None
    assert parse_summary("{broken json") is None


def test_say_date() -> None:
    assert say_date(TODAY, TODAY) == "сегодня"
    assert say_date(TODAY - timedelta(days=1), TODAY) == "вчера"
    assert say_date(TODAY - timedelta(days=3), TODAY) == "3 дня назад (4 октября)"
    assert say_date(TODAY + timedelta(days=2), TODAY) == "9 октября"


# -- store --------------------------------------------------------------------------------


async def test_follow_ups_due_stale_and_closed(app: Aion) -> None:
    store = EpisodeStore(app.db)
    await store.add_follow_up("собеседование", "2026-10-09", None)
    await store.add_follow_up("как спина", None, None)
    await store.add_follow_up("старая поездка", "2026-09-01", None)
    assert [f.topic for f in await store.open_follow_ups(TODAY)] == ["как спина"]
    due = await store.open_follow_ups(date(2026, 10, 9))
    assert [f.topic for f in due] == ["собеседование", "как спина"]
    await store.close([due[0].id])
    assert [f.topic for f in await store.open_follow_ups(date(2026, 10, 9))] == ["как спина"]


async def test_search_tolerates_word_forms(app: Aion) -> None:
    store = EpisodeStore(app.db)
    # fixed times within today: "2 hours ago" is yesterday when the test runs after midnight
    morning = datetime.combine(date.today(), datetime.min.time()).timestamp() + 60
    await store.add(morning, morning + 200, "Пользователь рассказал про отпуск в Турции.")
    await store.add(morning + 600, morning + 700, "Обсуждали новый ноутбук для работы.")
    found = await store.search("отпуске")
    assert [e.summary for e in found] == ["Пользователь рассказал про отпуск в Турции."]
    assert len(await store.on_day(date.today())) == 2


# -- diary ----------------------------------------------------------------------------------


def summarizer(answer: dict[str, Any]) -> FakeProvider:
    def script(_m: list[Message], _t: list[ToolDef]) -> list[StreamEvent]:
        return text_reply(json.dumps(answer, ensure_ascii=False))

    return FakeProvider(script)


async def talk(app: Aion, *lines: str) -> None:
    for i, line in enumerate(lines):
        if i % 2 == 0:
            await app.bus.publish(SpeechRecognized(text=line))
        else:
            await app.bus.publish(AssistantReply(text=line, final=False))
            await app.bus.publish(AssistantReply(text="", final=True))


async def test_conversation_becomes_diary_entry(app: Aion) -> None:
    store = app.diary.store
    await store.add_follow_up("переезд", None, None)
    (old,) = await store.open_follow_ups(date.today())
    provider = summarizer(
        {
            "summary": "Пользователь волнуется перед собеседованием, переезд закончил.",
            "follow_ups": [{"topic": "собеседование в банке", "when": "сегодня"}],
            "closed": [old.id],
        }
    )
    app.llm = provider
    await talk(
        app,
        "в пятницу у меня собеседование",
        "Удачи! Волнуетесь?",
        "немного, а переезд я уже закончил",
        "Поздравляю!",
    )
    await app.diary.close_session()

    prompt = provider.calls[0][0][0].content
    assert "Пользователь: в пятницу у меня собеседование" in prompt
    assert f"{old.id}: переезд" in prompt
    (episode,) = await store.recent()
    assert episode.summary.startswith("Пользователь волнуется")
    assert [f.topic for f in await store.open_follow_ups(date.today())] == ["собеседование в банке"]
    assert app.diary.lines == []


async def test_short_exchange_is_not_recorded(app: Aion) -> None:
    provider = summarizer({"summary": "x"})
    app.llm = provider
    await talk(app, "который час", "18:30.")
    await app.diary.close_session()
    assert provider.calls == []
    assert await app.diary.store.recent() == []


async def test_diary_goes_into_system_prompt_and_recall_tool(app: Aion) -> None:
    store = app.diary.store
    yesterday = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    started = yesterday.replace(hour=20).timestamp()
    await store.add(started, started + 600, "Обсуждали отпуск в Турции.")
    await store.add_follow_up("собеседование", date.today().isoformat(), None)
    brain = Brain(app, summarizer({"summary": "x"}))
    system = await brain.system_prompt()
    assert "- вчера: Обсуждали отпуск в Турции." in system
    assert "- собеседование (сегодня)" in system
    brain.close()

    recall = next(t for t in app.plugins.tools() if t.name == "recall_conversations")
    assert await recall.invoke({"query": "турция"}) == "вчера: Обсуждали отпуск в Турции."
    assert await recall.invoke({"days_ago": 1}) == "вчера: Обсуждали отпуск в Турции."
    assert await recall.invoke({"days_ago": 3}) == "В дневнике ничего такого нет"


# -- companion uses the diary -------------------------------------------------------------

watch = load_builtin_module("companion", "watch")


def test_companion_asks_due_follow_up_once() -> None:
    w = watch.Watch(watch.Settings(silence_minutes=10, break_minutes=0))
    start = datetime(2026, 10, 7, 14).timestamp()

    def check(at: float) -> list[Any]:
        return w.check(
            watch.Situation(
                now=at, hour=14, idle_s=5, fullscreen=False, boredom=0.5,
                follow_ups=["собеседование (сегодня)"],
            )
        )  # fmt: skip

    check(start)
    (obs,) = check(start + 11 * 60)
    assert obs.text.endswith("спроси, как дела с этим: собеседование (сегодня)")
    (again,) = check(start + 22 * 60)
    assert "собеседование" not in again.text


async def test_due_follow_up_is_suggested_once_at_conversation_start(app: Aion) -> None:
    await app.diary.store.add_follow_up("собеседование", date.today().isoformat(), None)
    provider = FakeProvider(lambda _m, _t: text_reply("Привет! Как собеседование?"))
    app.llm = provider
    app.brain = Brain(app, provider)
    app.dialog.fallback = app.brain.respond
    await app.dialog.submit("расскажи что-нибудь")
    await app.dialog.wait()
    await app.dialog.submit("а ещё что")
    await app.dialog.wait()
    first, second = (call[0][-1].content for call in provider.calls)
    assert "давно хотел узнать: собеседование" in first
    assert "собеседование" not in second
    # the hint is not stored in the dialog history
    assert app.brain.memory.messages()[0].content == "расскажи что-нибудь"
