from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.core.events import AssistantReply, EmotionChanged, ToolCalled
from aion.llm.base import (
    LlmError,
    LlmProvider,
    Message,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolDef,
    TurnEnd,
)
from aion.llm.brain import Brain
from aion.llm.memory import ShortTermMemory
from aion.llm.streamtext import EmotionTag, SentenceSplitter, ThinkFilter, clean_for_speech
from tests.helpers import say, write_plugin

# -- stream text processing ------------------------------------------------------------


def feed_all(splitter: SentenceSplitter, chunks: list[str]) -> list[str]:
    out = [s for c in chunks for s in splitter.feed(c)]
    return out + splitter.flush()


def test_sentence_splitter_streams_sentences() -> None:
    chunks = ["Добрый ве", "чер, сэр. Сегод", "ня 5 окт. 2026 г. прохладно! Что ", "ещё?"]
    assert feed_all(SentenceSplitter(), chunks) == [
        "Добрый вечер, сэр.",
        "Сегодня 5 окт. 2026 г. прохладно!",
        "Что ещё?",
    ]


def test_sentence_splitter_keeps_decimals_and_short_bits() -> None:
    assert feed_all(SentenceSplitter(), ["Ровно 3.5 км. Да. Именно так."]) == [
        "Ровно 3.5 км.",
        "Да. Именно так.",
    ]


def test_sentence_splitter_cuts_long_sentences() -> None:
    long = "слово, " * 60
    parts = feed_all(SentenceSplitter(max_chars=100), [long])
    assert len(parts) > 2
    assert all(len(p) <= 101 for p in parts)


def test_clean_for_speech() -> None:
    md = "## Итог\n- **Первое**: `код`\n- второе [ссылка](http://x.y)"
    assert clean_for_speech(md) == "Итог Первое: код второе ссылка"


def test_think_filter_streaming() -> None:
    f = ThinkFilter()
    chunks = ["<thi", "nk>секрет</th", "ink>Отв", "ет <", "b>"]
    assert "".join(f.feed(c) for c in chunks) + f.flush() == "Ответ <b>"


def test_emotion_tag() -> None:
    tag = EmotionTag()
    out = tag.feed("[jo") + tag.feed("y] Отлично!") + tag.flush()
    assert out == "Отлично!"
    assert tag.emotion == "joy"
    plain = EmotionTag()
    assert plain.feed("Просто текст") == "Просто текст"
    assert plain.emotion is None


def test_short_term_memory_limits_and_resets() -> None:
    mem = ShortTermMemory(max_turns=2, idle_reset_s=1000)
    for i in range(3):
        mem.add_turn([Message("user", f"q{i}"), Message("assistant", f"a{i}")])
    assert [m.content for m in mem.messages()] == ["q1", "a1", "q2", "a2"]
    mem.idle_reset_s = 0
    mem._last -= 1  # pyright: ignore[reportPrivateUsage]
    assert mem.messages() == []


# -- brain with a scripted provider ----------------------------------------------------

Script = Callable[[list[Message], list[ToolDef]], list[StreamEvent]]


class FakeProvider(LlmProvider):
    name = "fake"

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: list[tuple[list[Message], str, list[ToolDef]]] = []

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[ToolDef],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append((list(messages), system, tools))
        for event in self.script(messages, tools):
            yield event


def text_reply(*chunks: str) -> list[StreamEvent]:
    return [*(TextDelta(c) for c in chunks), TurnEnd(Message("assistant", "".join(chunks)))]


@pytest.fixture
def use_brain(app: Aion) -> Callable[[Script], FakeProvider]:
    def install(script: Script) -> FakeProvider:
        provider = FakeProvider(script)
        app.llm = provider
        app.brain = Brain(app, provider)
        app.dialog.fallback = app.brain.respond
        return provider

    return install


async def test_free_question_streams_sentences(app: Aion, use_brain: Any) -> None:
    replies: list[AssistantReply] = []
    emotions: list[str] = []
    app.bus.subscribe(AssistantReply, replies.append)
    app.bus.subscribe(EmotionChanged, lambda e: emotions.append(e.emotion))
    use_brain(lambda _m, _t: text_reply("[joy] Конечно, ", "сэр. Луне около ", "4,5 млрд лет."))

    assert await say(app, "сколько лет луне") == ["Конечно, сэр.", "Луне около 4,5 млрд лет."]
    assert [r.final for r in replies] == [False, False, True]
    assert "joy" in emotions
    assert app.dialog.last_reply == "Конечно, сэр. Луне около 4,5 млрд лет."


async def test_system_prompt_has_persona_date_and_facts(app: Aion, use_brain: Any) -> None:
    provider = use_brain(lambda _m, _t: text_reply("Хорошо."))
    await say(app, "запомни что я пью кофе без сахара")
    await say(app, "расскажи что-нибудь")
    _, system, _ = provider.calls[-1]
    assert "Aion" in system
    assert "сэр" in system
    assert "пью кофе без сахара" in system


async def test_tool_calling_loop(app: Aion, use_brain: Any) -> None:
    called: list[ToolCalled] = []
    app.bus.subscribe(ToolCalled, called.append)

    def script(messages: list[Message], tools: list[ToolDef]) -> list[StreamEvent]:
        names = {t.name for t in tools}
        assert "calc__calculate" in names
        if messages[-1].role == "tool":
            return text_reply(f"Получается {messages[-1].content}.")
        call = ToolCall("c1", "calc__calculate", {"expression": "17 * 23"})
        return [TurnEnd(Message("assistant", "", tool_calls=[call]))]

    use_brain(script)
    assert await say(app, "умножь в уме семнадцать на двадцать три") == ["Получается 391."]
    assert called[0].tool == "calculate"
    assert called[0].arguments == {"expression": "17 * 23"}


async def test_history_is_kept_between_turns(app: Aion, use_brain: Any) -> None:
    provider = use_brain(lambda m, _t: text_reply(f"Ответ {len(m)}."))
    await say(app, "первый вопрос")
    await say(app, "второй вопрос")
    messages, _, _ = provider.calls[-1]
    # only the latest message carries the mood note: the history stays cache-friendly
    assert [m.content for m in messages[:2]] == ["первый вопрос", "Ответ 1."]
    assert messages[2].content.startswith("второй вопрос\n\n[Твоё состояние сейчас:")


async def test_dangerous_tool_needs_confirmation(app_store: ConfigStore, plugins_dir: Path) -> None:
    write_plugin(
        plugins_dir,
        "power",
        """
        from aion.sdk import Plugin, tool
        class Power(Plugin):
            done = []
            @tool("Выключить компьютер", dangerous=True)
            async def shutdown(self) -> str:
                Power.done.append(1)
                return "выключаю"
        """,
    )
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    aion.dialog.ask_timeout = 0.2

    def script(messages: list[Message], _tools: list[ToolDef]) -> list[StreamEvent]:
        if messages[-1].role == "tool":
            return text_reply(f"Итог: {messages[-1].content}.")
        return [
            TurnEnd(Message("assistant", "", tool_calls=[ToolCall("1", "power__shutdown", {})]))
        ]

    async with aion:
        provider = FakeProvider(script)
        aion.llm, aion.brain = provider, Brain(aion, provider)
        aion.dialog.fallback = aion.brain.respond
        replies = await say(aion, "отключи всё, я спать")  # no answer to the confirmation
        assert replies[-1] == "Итог: Пользователь отменил действие."
        inst = aion.plugins.instance("power")
        assert inst is not None
        assert type(inst).done == []  # pyright: ignore[reportAttributeAccessIssue]


async def test_provider_error_is_spoken(app: Aion, use_brain: Any) -> None:
    def script(_m: list[Message], _t: list[ToolDef]) -> list[StreamEvent]:
        raise LlmError("Ollama не запущена")

    use_brain(script)
    (reply,) = await say(app, "в чём смысл жизни")
    assert "Ollama не запущена" in reply


# -- providers: message conversion -----------------------------------------------------


def test_anthropic_conversion_groups_tool_results() -> None:
    pytest.importorskip("anthropic")
    from aion.llm.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider("claude-opus-5-5", api_key_env="AION_TEST_NO_KEY")
    history = [
        Message("user", "погода и время?"),
        Message(
            "assistant",
            "Секунду.",
            tool_calls=[ToolCall("a", "weather__get_weather", {}), ToolCall("b", "clock__now", {})],
        ),
        Message("tool", "+5", tool_call_id="a"),
        Message("tool", "12:00", tool_call_id="b"),
    ]
    converted = provider._convert(history)  # pyright: ignore[reportPrivateUsage]
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
    assert [b["type"] for b in converted[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert [b["tool_use_id"] for b in converted[2]["content"]] == ["a", "b"]

    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "x"}]
    replay = provider._convert([Message("assistant", "x", raw=raw, raw_provider="anthropic")])  # pyright: ignore[reportPrivateUsage]
    assert replay[0]["content"] == raw


def test_anthropic_fallback_blocks_are_sanitized() -> None:
    pytest.importorskip("anthropic")
    from types import SimpleNamespace

    from aion.llm.anthropic_provider import _after_fallback  # pyright: ignore[reportPrivateUsage]

    blocks = [
        SimpleNamespace(type="thinking"),
        SimpleNamespace(type="text", text="Нача"),
        SimpleNamespace(type="tool_use"),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text="ло"),
    ]
    assert [b.type for b in _after_fallback(blocks)] == ["text", "text"]


async def test_ollama_remote_server_is_not_autostarted() -> None:
    from aion.llm.ollama import OllamaProvider

    provider = OllamaProvider("http://192.0.2.1:11434", "qwen3:8b")  # TEST-NET, unreachable
    provider._client.timeout = httpx.Timeout(0.2)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(LlmError, match="недоступна"):
        await provider.ensure_server()
    await provider.aclose()


async def test_openai_status_error_includes_server_message() -> None:
    import httpx2  # the HTTP client the openai SDK is built on
    import openai

    from aion.llm.openai_compat import OpenAICompatProvider

    def handler(_request: httpx2.Request) -> httpx2.Response:
        error = {"message": "The model `old-model` does not exist", "code": "model_not_found"}
        return httpx2.Response(404, json={"error": error})

    provider = OpenAICompatProvider("http://llm.test/v1", "old-model", "AION_TEST_NO_KEY")
    provider._client = openai.AsyncOpenAI(  # pyright: ignore[reportPrivateUsage]
        base_url="http://llm.test/v1",
        api_key="test",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(LlmError, match=r"404: The model `old-model` does not exist$"):
        await provider.complete("hi")
    await provider.aclose()


def test_api_error_detail_shapes() -> None:
    from types import SimpleNamespace

    from aion.llm.base import api_error_detail

    anthropic_body = {"type": "error", "error": {"type": "not_found", "message": "model: x"}}
    assert api_error_detail(SimpleNamespace(body=anthropic_body)) == "model: x"
    assert api_error_detail(SimpleNamespace(body={"message": "bad\n request"})) == "bad request"
    assert api_error_detail(SimpleNamespace(body=None)) == ""
    assert len(api_error_detail(SimpleNamespace(body="x" * 500))) == 200


async def test_switching_away_from_ollama_unloads_its_model(app: Aion) -> None:
    from aion.llm.ollama import OllamaProvider

    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    old = OllamaProvider("http://ollama.test", "qwen3:8b")
    old._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # pyright: ignore[reportPrivateUsage]
    app.llm = old
    await app.reload_llm()  # the configured provider is "none"
    assert sent == [{"model": "qwen3:8b", "keep_alive": 0}]


def test_ollama_serves_same_model() -> None:
    from aion.llm.ollama import OllamaProvider

    current = OllamaProvider("http://127.0.0.1:11434/", "qwen3:8b")
    assert current.serves_same_model(OllamaProvider("http://127.0.0.1:11434", "qwen3:8b", 16384))
    assert not current.serves_same_model(OllamaProvider("http://127.0.0.1:11434", "qwen3:4b"))
    assert not current.serves_same_model(None)


def test_unknown_leading_tag_is_dropped() -> None:
    tag = EmotionTag()
    assert tag.feed("[timer] Таймер запущен.") + tag.flush() == "Таймер запущен."
    assert tag.emotion is None


async def test_name_is_remembered_from_conversation(app: Aion, use_brain: Any) -> None:
    use_brain(lambda _m, _t: text_reply("Приятно познакомиться!"))
    assert await say(app, "Меня, кстати, Данила зовут") == ["Приятно познакомиться!"]
    await say(app, "меня зовут Данила, мне 25 лет")
    assert app.brain is not None
    facts = await app.brain.facts.all()
    assert "Пользователя зовут Данила" in facts
    assert "Пользователю 25 лет" in facts


async def test_slow_model_says_no_filler(app: Aion) -> None:
    class Slow(FakeProvider):
        async def stream(
            self, messages: list[Message], **kwargs: Any
        ) -> AsyncIterator[StreamEvent]:  # type: ignore[override]
            await asyncio.sleep(0.2)
            for event in text_reply("Готово, подумала."):
                yield event

    provider = Slow(lambda _m, _t: [])
    app.llm, app.brain = provider, Brain(app, provider)
    app.dialog.fallback = app.brain.respond
    assert await say(app, "сложный вопрос") == ["Готово, подумала."]


def test_first_phrase_may_end_at_a_comma() -> None:
    text = "Знаешь, это очень интересный вопрос, и ответ на него простой. Вот так."
    chunks = [w + " " for w in text.split(" ")]
    assert feed_all(SentenceSplitter(first_clause_chars=28), chunks) == [
        "Знаешь, это очень интересный вопрос,",
        "и ответ на него простой.",
        "Вот так.",
    ]
    # without the option sentences are whole
    assert feed_all(SentenceSplitter(), chunks)[0] == (
        "Знаешь, это очень интересный вопрос, и ответ на него простой."
    )


async def test_warm_up_prefills_the_prompt(app: Aion, use_brain: Any) -> None:
    provider = use_brain(lambda _m, _t: text_reply("Привет!"))
    provider.prefix_cache = True
    assert app.brain is not None
    await app.brain.warm_up()
    messages, system, _ = provider.calls[-1]
    assert system == await app.brain.system_prompt()
    assert [m.content for m in messages] == ["Привет"]


def test_trailing_emotion_tag_is_not_spoken() -> None:
    tag = EmotionTag()
    chunks = ["Всё в порядке, спасибо. ", "А как вы? [neu", "tral]"]
    out = "".join(tag.feed(c) for c in chunks) + tag.flush()
    assert out.strip() == "Всё в порядке, спасибо. А как вы?"
    assert tag.emotion == "neutral"
    # brackets that are not tags stay
    other = EmotionTag()
    text = "Список [см. выше] и массив [1, 2]."
    assert other.feed(text) + other.flush() == text
    assert other.emotion is None


async def test_promised_action_is_retried_as_a_tool_call(
    app: Aion, use_brain: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = app.plugins.instance("system")
    assert system is not None
    control = __import__(type(system).__module__.rsplit(".", 1)[0] + ".control", fromlist=["x"])
    volumes: list[int] = []
    monkeypatch.setattr(control, "set_volume", lambda p: volumes.append(p) or p)

    def script(messages: list[Message], _tools: list[ToolDef]) -> list[StreamEvent]:
        last = messages[-1]
        if last.role == "tool":
            return text_reply(f"Готово, {last.content}. [joy]")
        if "не вызвал инструмент" in last.content:
            call = ToolCall("c1", "system__set_volume", {"percent": 0})
            return [TurnEnd(Message("assistant", "", tool_calls=[call]))]
        return text_reply("Сэр, я убавлю громкость. [neutral]")

    provider = use_brain(script)
    # no command for this wording: it goes to the model
    assert await say(app, "пусть музыку будет еле слышно") == ["Готово, Громкость 0%."]
    assert volumes == [0]
    assert len(provider.calls) == 3
    # the nudge was only for the retry; the history keeps the user's words
    assert app.brain is not None
    assert app.brain.memory.messages()[0].content == "пусть музыку будет еле слышно"


async def test_plain_answers_are_not_retried(app: Aion, use_brain: Any) -> None:
    joke = "Почему программисты путают Хэллоуин с Рождеством? [joy]"
    provider = use_brain(lambda _m, _t: text_reply(joke))
    assert await say(app, "расскажи шутку про программистов") == [
        "Почему программисты путают Хэллоуин с Рождеством?"
    ]
    assert len(provider.calls) == 1


def test_emoji_are_not_spoken() -> None:
    assert clean_for_speech("Привет! Рада, что ты здесь! 😊 Как ты? 🌟🎶") == (
        "Привет! Рада, что ты здесь! Как ты?"
    )
    assert clean_for_speech("Температура −5 °C, ветер 3 м/с.") == "Температура −5 °C, ветер 3 м/с."


def test_made_up_latin_tags_are_dropped() -> None:
    tag = EmotionTag()
    out = tag.feed("Привет! Что нового? [curious]") + tag.flush()
    assert out.strip() == "Привет! Что нового?"
    assert tag.emotion is None


async def test_same_question_gets_a_different_answer(app: Aion, use_brain: Any) -> None:
    answers = iter(["Всё хорошо, а у тебя как? [joy]", "Всё хорошо, а у тебя как? [joy]",
                    "Отлично, весь день думала о космосе! [joy]"])  # fmt: skip

    def script(messages: list[Message], _tools: list[ToolDef]) -> list[StreamEvent]:
        return text_reply(next(answers))

    provider = use_brain(script)
    assert await say(app, "как дела?") == ["Всё хорошо, а у тебя как?"]
    # the copy is dropped unspoken, the model is asked again with a note
    assert await say(app, "как дела?") == ["Отлично, весь день думала о космосе!"]
    second, third = provider.calls[1][0][-1].content, provider.calls[2][0][-1].content
    assert "Ты уже сказал: «Всё хорошо, а у тебя как?»" in second
    assert "Ты начинаешь повторяться" in third
