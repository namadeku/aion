"""The LLM "brain": free conversation with plugin tools, memory and a persona.

Used as the dialog fallback when no plugin command matched. The answer is streamed sentence by
sentence into speech synthesis, so the assistant starts talking while the model still writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncGenerator, Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, cast

from loguru import logger
from rapidfuzz import fuzz

from aion.core.events import AssistantReply, SpeechRecognized, ToolCalled
from aion.llm.base import (
    LlmError,
    LlmProvider,
    Message,
    StreamEvent,
    TextDelta,
    ToolDef,
    TurnEnd,
)
from aion.llm.memory import FactStore, ShortTermMemory
from aion.llm.streamtext import EmotionTag, SentenceSplitter, ThinkFilter
from aion.plugins.tools import ToolSpec

if TYPE_CHECKING:
    from aion.app import Aion
    from aion.config import Config
    from aion.core.dialog import Turn

MAX_TOOL_ROUNDS = 5
FIRST_CLAUSE_CHARS = 28  # a first phrase this long may end at a comma
COMMENT_MAX_TOKENS = 150  # remarks on events are one or two short phrases
GUARDED_SENTENCES = 3  # opening sentences checked for a promised or claimed action
# An opening that promises an action in words ("Сэр, я убавлю громкость") instead of a tool call.
_PROMISE = re.compile(
    # promised: "Сэр, я убавлю громкость" / "Позвольте мне найти…"
    r"\b(?:убавлю|прибавлю|уменьшу|увеличу|включаю|включу|выключаю|выключу|переключаю|"
    r"переключу|поставлю|ставлю|открываю|открою|запускаю|запущу|установлю|напомню|проверю|"
    r"проверим|посмотрю|посмотрим|узнаю|узнаем|найду|сделаю|позвольте мне|закрываю|закрою|"
    r"сворачиваю|сверну|печатаю|напечатаю|записываю|запишу|"
    # claimed as done: "Открыла Телеграм" with no tool call (seen with qwen3:8b)
    r"открыл\w*|закрыл\w*|включил\w*|выключил\w*|убавил\w*|прибавил\w*|уменьшил\w*|"
    r"увеличил\w*|переключил\w*|поставил\w*|запустил\w*|свернул\w*|напечатал\w*|"
    r"записал\w*|запомнил\w*|установил\w*|готово)\b",
    re.IGNORECASE,
)


REPEAT_SIMILAR = 88  # a first sentence this close to an earlier one is a repeat
SIMILAR_QUESTION = 80  # a question this close to an earlier one invites copying its answer
RECENT_REPLIES = 6  # how many earlier replies a new one is compared with
_TAG = re.compile(r"\[[\w-]{1,20}\]")


class _Retry(Exception):
    """The answer went wrong in its first words: drop it and ask again with ``nudge``."""

    def __init__(self, reason: str, nudge: str) -> None:
        super().__init__(reason)
        self.nudge = nudge


def _first_sentence(text: str) -> str:
    text = _TAG.sub("", text).strip()
    return re.split(r"(?<=[.!?…])\s", text, maxsplit=1)[0].lower()


WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

VOICE_RULES = (
    "Твои ответы звучат голосом, поэтому говори как в живом разговоре: обычно 1–4 предложения, "
    "без markdown, списков, таблиц, эмодзи и ссылок; числа можно писать цифрами. Начинай "
    "отвечать сразу, без вступлений вроде «Конечно!» в каждой фразе — разнообразь речь.\n"
    # The emotion tag goes LAST: asked to open with it, qwen3:8b started talking instead of
    # calling tools (0 of 10 tool calls; 5 of 10 with the tag at the end).
    "В самом конце ответа поставь тег эмоции в квадратных скобках — [joy], [surprise], "
    "[thinking], [sad], [angry] или [neutral]: он не озвучивается, а меняет мимику и позу "
    "твоего аватара.\n"
    "Если нужны свежие данные или действие (время, погода, таймеры, заметки, программы, "
    "громкость, музыка) — вызови подходящий инструмент, не выдумывай. Никогда не говори, что "
    "сделал действие (включил, открыл, поставил), если не вызвал для этого инструмент и не "
    "получил ответ; если такого инструмента нет — так и скажи. Когда собеседник рассказывает о "
    "себе что-то важное (имя, близкие, предпочтения, планы, события), сохрани это инструментом "
    "memory__remember_fact и вспоминай потом к месту.\n"
    "Не называй себя языковой моделью или программой без необходимости — ты {name}."
)


def create_provider(config: Config) -> LlmProvider | None:
    llm = config.llm
    match llm.provider:
        case "none":
            return None
        case "ollama":
            from aion.llm.ollama import OllamaProvider

            return OllamaProvider(llm.ollama.base_url, llm.ollama.model, llm.ollama.num_ctx)
        case "anthropic":
            from aion.llm.anthropic_provider import AnthropicProvider

            return AnthropicProvider(
                llm.anthropic.model, llm.anthropic.api_key_env, llm.anthropic.effort
            )
        case "openai":
            from aion.llm.openai_compat import OpenAICompatProvider

            return OpenAICompatProvider(
                llm.openai.base_url, llm.openai.model, llm.openai.api_key_env
            )


class Brain:
    def __init__(self, app: Aion, provider: LlmProvider) -> None:
        self.app = app
        self.provider = provider
        self.memory = ShortTermMemory(app.config.llm.history_turns)
        self.facts = FactStore(app.db)
        self._error_said_at = 0.0
        self._started_at = 0.0
        # command turns handled by plugins go into the dialog history too, so the model
        # knows what just happened ("поставь таймер" -> "а когда он сработает?")
        self._responding = False
        self._pending_user: str | None = None
        self._unsubscribe = [
            app.bus.subscribe(SpeechRecognized, self._on_user),
            app.bus.subscribe(AssistantReply, self._on_reply),
        ]

    def close(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()

    def _on_user(self, event: SpeechRecognized) -> None:
        if not self._responding:
            self._pending_user = event.text

    def _on_reply(self, event: AssistantReply) -> None:
        if self._responding or not event.text or self._pending_user is None:
            return
        turn = [Message("user", self._pending_user), Message("assistant", event.text)]
        self.memory.add_turn(turn)
        self._pending_user = None

    # -- system prompt --------------------------------------------------------------------

    async def system_prompt(self) -> str:
        config = self.app.config
        profile = config.profile
        persona = profile.persona.format(name=profile.name, address=config.assistant.user_address)
        now = datetime.now()
        # Only the date: the prompt (and the tool list after it) stays byte-identical for a day,
        # so Ollama reuses its cached prefix instead of re-reading thousands of tokens each turn.
        parts = [
            persona,
            VOICE_RULES.format(name=profile.name),
            f"Сегодня {now:%Y-%m-%d}, {WEEKDAYS[now.weekday()]}. "
            "Точное время узнавай инструментом clock__current_datetime.",
        ]
        facts = await self.facts.all()
        if facts:
            parts.append("Что ты знаешь о пользователе:\n" + "\n".join(f"- {f}" for f in facts))
        # changes only when a conversation ends or a day passes: the prompt cache survives
        if diary := await self.app.diary.context(now.date()):
            parts.append(diary)
        return "\n\n".join(parts)

    # -- tools ----------------------------------------------------------------------------

    def _tools(self) -> dict[str, ToolSpec]:
        if not self.app.config.llm.tools:
            return {}
        tools = [*self.app.plugins.tools(), *self.app.mcp.tools()]
        return {t.qualified_name: t for t in tools}

    async def _run_tool(
        self, turn: Turn, spec: ToolSpec | None, call_name: str, args: dict[str, Any]
    ) -> tuple[str, bool]:
        if spec is None:
            return f"Инструмент {call_name} не найден", True
        await self.app.bus.publish(ToolCalled(plugin=spec.plugin, tool=spec.name, arguments=args))
        address = self.app.config.assistant.user_address.capitalize()
        if spec.dangerous and not await turn.confirm(f"{address}, выполнить «{spec.description}»?"):
            return "Пользователь отменил действие", True
        ctx = self.app.plugins.context(turn, spec.plugin)
        try:
            return await spec.invoke(args, ctx), False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Ошибка инструмента {}", spec.qualified_name)
            return f"Ошибка: {e}", True

    # -- conversation ---------------------------------------------------------------------

    async def respond(self, turn: Turn) -> None:
        """Dialog fallback: answer ``turn.text`` with the LLM, speaking as it streams."""
        dialog = self.app.dialog
        spoken: list[str] = []
        await dialog.set_emotion("thinking")
        try:
            # a new conversation is the moment for "how did the interview go?"
            nudge = "" if self.memory.messages() else await self.app.diary.nudge(date.today())
            new_messages = await self._converse(
                turn, Message("user", turn.text), spoken, note=nudge, guard=True
            )
            if not spoken:
                await dialog.say_chunk("Готово.")
            self.memory.add_turn(new_messages)
        except LlmError as e:
            logger.warning("LLM недоступна: {}", e)
            await self._say_error(str(e))
        finally:
            await dialog.end_reply(" ".join(spoken))
            await dialog.set_emotion("neutral")

    async def comment(self, turn: Turn, prompt: str, remember: str, timeout: float) -> bool:
        """Say an unprompted remark on an event (``prompt`` explains what happened).

        Gives up silently (returns False) if the model fails or its first phrase takes longer
        than ``timeout`` — the caller then says a ready-made phrase. ``remember`` is what goes
        into the dialog history instead of the long prompt, so the user can ask about it.
        """
        dialog = self.app.dialog
        spoken: list[str] = []
        try:
            async with asyncio.timeout(timeout) as deadline:
                new_messages = await self._converse(
                    turn,
                    Message("user", prompt),
                    spoken,
                    max_tokens=COMMENT_MAX_TOKENS,
                    on_first_phrase=lambda: deadline.reschedule(None),
                )
        except (LlmError, TimeoutError) as e:
            logger.info("LLM не прокомментировала событие: {}", e or "слишком долго")
            return bool(spoken)
        finally:
            await dialog.end_reply(" ".join(spoken))
        if spoken:
            new_messages[0] = Message("user", remember)
            self.memory.add_turn(new_messages)
        return bool(spoken)

    async def _converse(
        self,
        turn: Turn,
        message: Message,
        spoken: list[str],
        *,
        max_tokens: int | None = None,
        on_first_phrase: Callable[[], None] | None = None,
        note: str = "",
        guard: bool = False,
    ) -> list[Message]:
        """Stream the model's answer to ``message`` (running its tool calls) into speech.

        Returns the new messages of this exchange; spoken sentences are appended to ``spoken``.
        ``note`` is extra guidance sent with this message only (like the mood).
        """
        tools = self._tools()
        tool_defs = [ToolDef(name, t.description, t.parameters) for name, t in tools.items()]
        new_messages: list[Message] = [message]
        # The mood goes with this message only (the history keeps it clean): changing text
        # in the system prompt would invalidate the provider's prompt cache.
        notes = "\n".join(n for n in (self.app.mood.note(), note) if n)
        sent = [Message("user", f"{message.content}\n\n{notes}")]
        self._responding = True
        self._pending_user = None
        self._started_at = time.perf_counter()
        history = self.memory.messages()
        avoid: list[str] = []
        if guard:
            earlier, similar = self._earlier_replies(history, message.content)
            avoid = [_first_sentence(r) for r in earlier]
            if similar:  # the same question again: small models copy their last answer
                sent = [Message("user", f"{sent[0].content}\n{self._repeat_note(similar)}")]
        retried: set[str] = set()
        try:
            system = await self.system_prompt()
            for _ in range(MAX_TOOL_ROUNDS):
                fresh = len(new_messages) == 1  # after a tool ran, its result decides the words
                try:
                    end = await self._stream_round(
                        history + sent + new_messages[1:],
                        system,
                        tool_defs,
                        spoken,
                        max_tokens=max_tokens,
                        on_first_phrase=on_first_phrase,
                        guard=guard and fresh and bool(tool_defs) and "promise" not in retried,
                        avoid=avoid if fresh and "repeat" not in retried else [],
                    )
                except _Retry as retry:
                    # Asked once more with a nudge, the model calls the tool (10 of 10 in a
                    # test) or rephrases; the dropped words were never spoken.
                    logger.info("Ответ LLM отброшен ({}), повторяю", retry)
                    retried.add(str(retry).split(":", 1)[0])
                    sent = [Message("user", f"{sent[0].content}\n{retry.nudge}")]
                    continue
                if end is None:
                    break
                new_messages.append(end.message)
                if not end.message.tool_calls:
                    break
                for call in end.message.tool_calls:
                    logger.info("LLM вызывает {}({})", call.name, call.arguments)
                    result, is_error = await self._run_tool(
                        turn, tools.get(call.name), call.name, call.arguments
                    )
                    new_messages.append(
                        Message("tool", result, tool_call_id=call.id, is_error=is_error)
                    )
        finally:
            self._responding = False
        return new_messages

    async def _stream_round(
        self,
        messages: list[Message],
        system: str,
        tool_defs: list[ToolDef],
        spoken: list[str],
        *,
        max_tokens: int | None = None,
        on_first_phrase: Callable[[], None] | None = None,
        guard: bool = False,
        avoid: list[str] | None = None,
    ) -> TurnEnd | None:
        """One model response, spoken sentence by sentence as it arrives.

        Some openings are not spoken but raise :class:`_Retry` so the caller asks again:
        with ``guard``, one that promises an action ("убавлю громкость") instead of calling
        a tool; with ``avoid``, one that repeats the start of an earlier reply.
        """
        llm = self.app.config.llm
        dialog = self.app.dialog
        # the very first phrase may end at a comma: speech starts sooner
        first_clause = 0 if spoken else FIRST_CLAUSE_CHARS
        think, emotion = ThinkFilter(), EmotionTag()
        splitter = SentenceSplitter(first_clause_chars=first_clause)
        end: TurnEnd | None = None

        async def speak(sentences: list[str]) -> None:
            for raw in sentences:
                # the first one capitalized: "привет, ..." comes from the model at times
                sentence = raw if spoken else raw[:1].upper() + raw[1:]
                if guard and len(spoken) < GUARDED_SENTENCES and _PROMISE.search(sentence):
                    raise _Retry(f"promise: {sentence}", self._promise_nudge())
                if avoid and not spoken and self._repeats(sentence, avoid):
                    raise _Retry(f"repeat: {sentence}", self._repeat_note([sentence], again=True))
                if not spoken:
                    logger.info("Первая фраза ответа через {:.2f} с", self._elapsed())
                    if on_first_phrase is not None:
                        on_first_phrase()
                spoken.append(sentence)
                await dialog.say_chunk(sentence)

        async def show_emotion() -> None:
            if emotion.emotion:
                await dialog.set_emotion(emotion.emotion)
                emotion.emotion = None

        # Tools are passed even when not wanted: they are part of the cached prompt prefix.
        stream = self.provider.stream(
            messages,
            system=system,
            tools=tool_defs,
            temperature=llm.temperature,
            max_tokens=max_tokens or llm.max_tokens,
        )
        # closed explicitly: a guard may abandon it midway, and the model should stop writing
        async with contextlib.aclosing(cast("AsyncGenerator[StreamEvent]", stream)) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    text = emotion.feed(think.feed(event.text))
                    await show_emotion()
                    await speak(splitter.feed(text))
                else:
                    end = event
        tail = emotion.feed(think.flush()) + emotion.flush()
        await show_emotion()  # the tag usually comes last
        await speak(splitter.feed(tail) + splitter.flush())
        return end

    def _promise_nudge(self) -> str:
        female = self.app.config.profile.gender == "female"
        promised, called = ("пообещала", "вызвала") if female else ("пообещал", "вызвал")
        return (
            f"[Ты {promised} действие, но не {called} инструмент. Вызови подходящий "
            "инструмент сейчас, без текста; если подходящего нет — честно скажи, что не можешь.]"
        )

    @staticmethod
    def _earlier_replies(history: list[Message], question: str) -> tuple[list[str], list[str]]:
        """Recent assistant replies, and those that answered a question like this one."""
        replies: list[str] = []
        similar: list[str] = []
        asked = ""
        for message in history:
            if message.role == "user":
                asked = message.content
            elif message.role == "assistant" and message.content.strip():
                reply = _TAG.sub("", message.content).strip()
                replies.append(reply)
                if fuzz.token_set_ratio(asked.lower(), question.lower()) >= SIMILAR_QUESTION:
                    similar.append(reply)
        return replies[-RECENT_REPLIES:], similar[-2:]

    @staticmethod
    def _repeats(sentence: str, avoid: list[str]) -> bool:
        first = _first_sentence(sentence)
        return len(first) > 12 and any(fuzz.ratio(first, a) >= REPEAT_SIMILAR for a in avoid)

    def _repeat_note(self, replies: list[str], *, again: bool = False) -> str:
        female = self.app.config.profile.gender == "female"
        said = "сказала" if female else "сказал"
        quoted = "; ".join(f"«{r[:200]}»" for r in replies)
        start = f"Ты начинаешь повторяться: {quoted}." if again else f"Ты уже {said}: {quoted}."
        return f"[{start} Не повторяй это — ответь иначе: другими словами, с другой мыслью.]"

    def _elapsed(self) -> float:
        return time.perf_counter() - self._started_at

    async def warm_up(self) -> None:
        """Load the model and pre-read the system prompt with tools (prompt-cache prefill).

        The first real question then only processes the user's words, not the whole prompt.
        """
        warm = getattr(self.provider, "warm_up", None)
        if warm is not None:
            await warm()
        if not getattr(self.provider, "prefix_cache", False):
            return
        started = time.perf_counter()
        tools = self._tools()
        tool_defs = [ToolDef(name, t.description, t.parameters) for name, t in tools.items()]
        system = await self.system_prompt()
        async for _ in self.provider.stream(
            [Message("user", "Привет")],
            system=system,
            tools=tool_defs,
            temperature=self.app.config.llm.temperature,
            max_tokens=1,
        ):
            pass
        logger.info("Промт LLM прочитан заранее за {:.1f} с", time.perf_counter() - started)

    async def _say_error(self, error: str) -> None:
        # Explain once in a while; otherwise a short apology to avoid repeating a long message.
        if time.monotonic() - self._error_said_at > 120:
            self._error_said_at = time.monotonic()
            await self.app.dialog.say_chunk(f"Языковая модель недоступна. {error}")
        else:
            await self.app.dialog.say_chunk("Языковая модель всё ещё недоступна.")

    async def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int | None = None
    ) -> str:
        return await self.provider.complete(prompt, system=system, max_tokens=max_tokens)
