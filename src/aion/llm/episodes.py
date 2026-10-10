"""Episodic memory: a diary of past conversations and things to ask about later.

Facts (``FactStore``) say *who* the user is; the diary says *what happened*. When a
conversation ends (a long pause, or the assistant exits), the LLM writes a short entry and
picks out upcoming events worth a question later ("собеседование 9 октября"). The latest
entries and the due questions go into the system prompt, so the character can say "как прошло
собеседование?" on its own; the companion plugin uses them to start a conversation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from loguru import logger
from rapidfuzz import fuzz

from aion.core.events import AssistantReply, SpeechRecognized
from aion.storage import Database
from aion.text import normalize
from aion.text.timeparse import parse_day

if TYPE_CHECKING:
    from aion.app import Aion

SESSION_GAP_S = 30 * 60  # a pause this long ends a conversation (as in ShortTermMemory)
CHECK_S = 60.0
MIN_USER_LINES = 2  # shorter exchanges ("который час?") are not worth a diary entry
TRANSCRIPT_CHARS = 8000  # the summarizer sees at most this much of the conversation
FOLLOW_UP_DAYS = 7  # a question is stale this many days after its date
SUMMARY_TIMEOUT_S = 60.0
SHUTDOWN_TIMEOUT_S = 15.0

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


@dataclass(frozen=True)
class Episode:
    id: int
    started: float
    ended: float
    summary: str


@dataclass(frozen=True)
class FollowUp:
    id: int
    topic: str
    due: str | None


def say_date(day: date, today: date) -> str:
    """``сегодня``, ``вчера``, ``3 дня назад (4 октября)``, ``9 октября``."""
    delta = (today - day).days
    plain = f"{day.day} {MONTHS[day.month - 1]}"
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "вчера"
    if delta == -1:
        return "завтра"
    if 2 <= delta <= 6:
        return f"{delta} {'дня' if delta < 5 else 'дней'} назад ({plain})"
    return plain


class EpisodeStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(self, started: float, ended: float, summary: str) -> int:
        return await self.db.execute(
            "INSERT INTO episodes (started, ended, summary) VALUES (?, ?, ?)",
            (started, ended, summary),
        )

    async def recent(self, limit: int = 5) -> list[Episode]:
        rows = await self.db.fetchall(
            "SELECT * FROM episodes ORDER BY ended DESC LIMIT ?", (limit,)
        )
        return [Episode(**r) for r in reversed(rows)]

    async def on_day(self, day: date) -> list[Episode]:
        start = datetime.combine(day, datetime.min.time()).timestamp()
        rows = await self.db.fetchall(
            "SELECT * FROM episodes WHERE started >= ? AND started < ? ORDER BY started",
            (start, start + 86400),
        )
        return [Episode(**r) for r in rows]

    async def search(self, query: str, limit: int = 5) -> list[Episode]:
        """Entries most similar to ``query`` (fuzzy, tolerant to word forms)."""
        needle = normalize(query, numbers=False)
        rows = await self.db.fetchall("SELECT * FROM episodes ORDER BY ended DESC LIMIT 500")
        scored = [
            (fuzz.partial_token_set_ratio(needle, normalize(r["summary"], numbers=False)), r)
            for r in rows
        ]
        best = sorted((s for s in scored if s[0] >= 70), key=lambda s: -s[0])[:limit]
        return [Episode(**r) for _, r in sorted(best, key=lambda s: s[1]["started"])]

    async def add_follow_up(self, topic: str, due: str | None, episode: int | None) -> None:
        await self.db.execute(
            "INSERT INTO follow_ups (topic, due, episode) VALUES (?, ?, ?)", (topic, due, episode)
        )

    async def open_follow_ups(self, today: date) -> list[FollowUp]:
        """Questions to ask now: their day has come and they are not stale yet."""
        stale = (today - timedelta(days=FOLLOW_UP_DAYS)).isoformat()
        await self.db.execute(
            "UPDATE follow_ups SET closed = 1 WHERE closed = 0 AND due IS NOT NULL AND due < ?",
            (stale,),
        )
        rows = await self.db.fetchall(
            "SELECT id, topic, due FROM follow_ups "
            "WHERE closed = 0 AND (due IS NULL OR due <= ?) ORDER BY id",
            (today.isoformat(),),
        )
        return [FollowUp(**r) for r in rows]

    async def close(self, ids: list[int]) -> None:
        for follow_up in ids:
            await self.db.execute("UPDATE follow_ups SET closed = 1 WHERE id = ?", (follow_up,))

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM episodes")
        await self.db.execute("DELETE FROM follow_ups")


def similar(a: str, b: str) -> bool:
    return fuzz.token_set_ratio(normalize(a, numbers=False), normalize(b, numbers=False)) >= 80


@dataclass
class Summary:
    text: str
    follow_ups: list[tuple[str, str | None]]  # (topic, when in words: "в пятницу")
    closed: list[int]


def parse_summary(raw: str) -> Summary | None:
    """Read the summarizer's JSON; tolerate prose or code fences around it."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data: Any = None
    if match:
        with contextlib.suppress(ValueError):
            data = json.loads(match.group(0))
    if not isinstance(data, dict):
        text = raw.strip()
        return Summary(text, [], []) if text and "{" not in text else None
    fields = cast(dict[str, Any], data)
    summary = str(fields.get("summary") or "").strip()
    if not summary:
        return None
    follow_ups: list[tuple[str, str | None]] = []
    for item in _list(fields.get("follow_ups")):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        topic = str(entry.get("topic") or "").strip()
        if topic:
            when = str(entry.get("when") or "").strip()
            follow_ups.append((topic, when if when.lower() not in ("", "null") else None))
    closed = [int(i) for i in _list(fields.get("closed")) if str(i).isdigit()]
    return Summary(summary, follow_ups, closed)


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


class Diary:
    """Records the current conversation and writes a diary entry when it ends."""

    def __init__(self, app: Aion) -> None:
        self.app = app
        self.store = EpisodeStore(app.db)
        self.lines: list[tuple[float, str, str]] = []  # (time, role, text)
        self._reply: list[str] = []
        self.raised: set[int] = set()  # follow-ups already suggested to the model
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe = [
            app.bus.subscribe(SpeechRecognized, self._on_user),
            app.bus.subscribe(AssistantReply, self._on_reply),
        ]

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="diary")

    async def stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
                await self.close_session()

    def _on_user(self, event: SpeechRecognized) -> None:
        self.lines.append((event.ts, "user", event.text))

    def _on_reply(self, event: AssistantReply) -> None:
        if event.text:
            self._reply.append(event.text)
        if event.final and self._reply:
            self.lines.append((event.ts, "assistant", " ".join(self._reply)))
            self._reply = []

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_S)
            if self.lines and time.time() - self.lines[-1][0] >= SESSION_GAP_S:
                await self.close_session()

    async def close_session(self) -> None:
        """Write the finished conversation into the diary (if it is worth it)."""
        lines, self.lines = self.lines, []
        if sum(1 for _, role, _ in lines if role == "user") < MIN_USER_LINES:
            return
        llm = self.app.llm
        if llm is None:
            return
        today = date.today()
        open_ups = await self.store.open_follow_ups(today + timedelta(days=365))
        prompt = self.prompt(lines, open_ups, today)
        try:
            async with asyncio.timeout(SUMMARY_TIMEOUT_S):
                raw = await llm.complete(prompt, max_tokens=500)
        except Exception as e:
            logger.warning("Не удалось записать разговор в дневник: {}", e or "таймаут")
            return
        summary = parse_summary(raw)
        if summary is None:
            logger.warning("Дневник: модель ответила не по формату: {!r}", raw[:200])
            return
        episode = await self.store.add(lines[0][0], lines[-1][0], summary.text)
        known = [f.topic for f in open_ups]
        said_on = date.fromtimestamp(lines[-1][0])
        for topic, when in summary.follow_ups:
            # small models re-list topics they were shown, even the ones just closed
            if any(similar(topic, other) for other in known):
                continue
            # the model only quotes the day: counting weekdays is not its strong side
            due = parse_day(when, said_on) if when else None
            await self.store.add_follow_up(topic, due.isoformat() if due else None, episode)
            known.append(topic)
        await self.store.close([i for i in summary.closed if i in {f.id for f in open_ups}])
        logger.info(
            "Дневник: {} (спросить потом: {})",
            summary.text,
            ", ".join(f"{t} [{d or '—'}]" for t, d in summary.follow_ups) or "нет",
        )

    def prompt(
        self, lines: list[tuple[float, str, str]], open_ups: list[FollowUp], today: date
    ) -> str:
        name = self.app.config.profile.name
        speaker = {"user": "Пользователь", "assistant": name}
        transcript = "\n".join(
            f"[{datetime.fromtimestamp(ts):%H:%M}] {speaker[role]}: {text}"
            for ts, role, text in lines
        )[-TRANSCRIPT_CHARS:]  # the end of a long conversation matters most
        pending = "\n".join(f"{f.id}: {f.topic}" for f in open_ups) or "нет"
        return (
            f"Ты ведёшь дневник разговоров ассистента {name} с пользователем. "
            f"Сегодня {today.isoformat()}, {WEEKDAYS[today.weekday()]}.\n\n"
            f"Разговор:\n{transcript}\n\n"
            f"Темы, о которых раньше собирались спросить (id: тема):\n{pending}\n\n"
            "Ответь только JSON, без пояснений:\n"
            '{"summary": "1–3 предложения от третьего лица: о чём говорили, что нового '
            'узнали о пользователе, его настроение",\n'
            ' "follow_ups": [{"topic": "о чём спросить потом, коротко", '
            '"when": "когда это будет — словами из разговора: «завтра», «в пятницу», '
            '«15 октября», «через неделю»; или null"}],\n'
            ' "closed": [id тем из списка выше, которые уже обсудили в этом разговоре]}\n'
            "follow_ups — только будущие события и незаконченные дела пользователя (встреча, "
            "экзамен, поездка, болезнь, важная покупка), обычно 0–2; темы из списка выше в "
            "follow_ups не повторяй. Реплики о том, что делает сам ассистент, не нужны."
        )

    async def nudge(self, today: date) -> str:
        """A due question to raise at the start of a conversation, once per topic per run."""
        for follow_up in await self.store.open_follow_ups(today):
            if follow_up.id not in self.raised:
                self.raised.add(follow_up.id)
                return (
                    f"[Ты давно хотела узнать: {follow_up.topic}. Если это к месту, спроси "
                    "об этом в своём ответе.]"
                    if self.app.config.profile.gender == "female"
                    else f"[Ты давно хотел узнать: {follow_up.topic}. Если это к месту, спроси "
                    "об этом в своём ответе.]"
                )
        return ""

    async def context(self, today: date) -> str:
        """Diary part of the system prompt: recent conversations and questions to ask."""
        parts: list[str] = []
        episodes = await self.store.recent(4)
        if episodes:
            parts.append(
                "Недавние разговоры (из твоего дневника):\n"
                + "\n".join(
                    f"- {say_date(date.fromtimestamp(e.started), today)}: {e.summary}"
                    for e in episodes
                )
            )
        follow_ups = await self.store.open_follow_ups(today)
        if follow_ups:
            parts.append(
                "При случае, к месту и не всё сразу, спроси пользователя, как дела с этим:\n"
                + "\n".join(
                    f"- {f.topic}"
                    + (f" ({say_date(date.fromisoformat(f.due), today)})" if f.due else "")
                    for f in follow_ups[:5]
                )
            )
        return "\n\n".join(parts)
