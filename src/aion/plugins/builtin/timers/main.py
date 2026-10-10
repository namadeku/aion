"""Timers, alarms and reminders persisted in plugin storage."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from aion.sdk import Context, Plugin, command, fmt_count, tool
from aion.text.timeparse import parse_clock, parse_duration

Kind = Literal["timer", "alarm", "reminder"]
_KEY = "items"


def say_duration(seconds: float) -> str:
    seconds = round(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(fmt_count(hours, ("час", "часа", "часов")))
    if minutes:
        parts.append(fmt_count(minutes, ("минута", "минуты", "минут")))
    if secs and not hours:
        parts.append(fmt_count(secs, ("секунда", "секунды", "секунд")))
    return " ".join(parts) or "0 секунд"


def next_clock(hour: int, minute: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


class Timers(Plugin):
    async def on_load(self) -> None:
        self._scheduled: dict[str, asyncio.Task[Any]] = {}
        items: list[dict[str, Any]] = await self.storage.get(_KEY, [])
        now = time.time()
        grace = float(self.config["missed_grace_hours"]) * 3600
        missed = [i for i in items if i["due"] <= now]
        for item in items:
            if item["due"] > now:
                self._schedule(item)
        if missed:
            await self.storage.set(_KEY, [i for i in items if i["due"] > now])
            recent = [i for i in missed if now - i["due"] <= grace]
            if recent:
                self.create_task(self._announce_missed(recent))

    # -- commands -------------------------------------------------------------------------

    @command(
        [
            "(поставь|заведи|установи|засеки|включи) таймер [на] {duration}",
            "таймер на {duration}",
        ]
    )
    async def timer(self, ctx: Context, duration: str) -> bool:
        parsed = parse_duration(duration)
        if parsed is None:
            # "таймер на время варки яйца" — not a duration: let the LLM work it out
            # (it can call the set_timer tool with the right number of seconds)
            return False
        await self._add("timer", time.time() + parsed.value, label=say_duration(parsed.value))
        await ctx.say(f"Таймер на {say_duration(parsed.value)} запущен.")
        return True

    @command(["(поставь|заведи|установи|засеки) таймер"])
    async def timer_ask(self, ctx: Context) -> None:
        answer = await ctx.ask("На сколько поставить таймер?")
        parsed = parse_duration(answer or "")
        if parsed is None:
            await ctx.say(ctx.g("Не понял длительность.", "Не поняла длительность."))
            return
        await self._add("timer", time.time() + parsed.value, label=say_duration(parsed.value))
        await ctx.say(f"Таймер на {say_duration(parsed.value)} запущен.")

    @command(
        [
            "(разбуди меня|поставь будильник|заведи будильник|установи будильник) {when}",
            "будильник {when}",
        ]
    )
    async def alarm(self, ctx: Context, when: str) -> None:
        clock = parse_clock(when)
        if clock is None:
            clock = parse_clock("в " + when)
        if clock is None:
            await ctx.say(ctx.g("Не понял", "Не поняла") + ", на какое время поставить будильник.")
            return
        hour, minute = clock.value
        due = next_clock(hour, minute)
        await self._add("alarm", due.timestamp(), label=f"{hour}:{minute:02d}")
        day = "завтра" if due.date() > datetime.now().date() else "сегодня"
        await ctx.say(f"Будильник установлен на {hour}:{minute:02d}, {day}.")

    @command(["напомни [мне] {request}"])
    async def remind(self, ctx: Context, request: str) -> None:
        due, text = self._parse_reminder(request)
        if due is None:
            answer = await ctx.ask("Когда напомнить?")
            due, extra = self._parse_reminder(answer or "")
            text = text or extra
        if due is None:
            await ctx.say(ctx.g("Не понял", "Не поняла") + ", когда напомнить.")
            return
        if not text:
            text = await ctx.ask("О чём напомнить?") or "вы просили напомнить"
        await self._add("reminder", due, label=text)
        when = datetime.fromtimestamp(due)
        await ctx.say(f"Хорошо, напомню в {when:%H:%M}: {text}.")

    @command(
        [
            "сколько осталось [на таймере]",
            "сколько осталось времени",
            "какие у меня (таймеры|напоминания|будильники)",
        ]
    )
    async def status(self, ctx: Context) -> None:
        items = sorted(await self.storage.get(_KEY, []), key=lambda i: i["due"])
        if not items:
            await ctx.say("Активных таймеров и напоминаний нет.")
            return
        now = time.time()
        parts = [self._describe(i, now) for i in items[:5]]
        await ctx.say("; ".join(parts) + ".")

    @command(
        [
            "(отмени|удали|сбрось|выключи|останови) (таймер|таймеры|будильник|будильники)",
            "(отмени|удали) (напоминание|напоминания)",
        ]
    )
    async def cancel(self, ctx: Context) -> None:
        text = ctx.text.lower()
        kind: Kind = (
            "alarm" if "будильник" in text else "reminder" if "напомин" in text else "timer"
        )
        items: list[dict[str, Any]] = await self.storage.get(_KEY, [])
        victims = [i for i in items if i["kind"] == kind]
        for item in victims:
            await self._remove(item["id"])
        await ctx.say("Отменено." if victims else "Отменять нечего.")

    # -- LLM tools ------------------------------------------------------------------------

    @tool("Поставить таймер")
    async def set_timer(self, seconds: Annotated[int, "Длительность в секундах"]) -> str:
        await self._add("timer", time.time() + seconds, label=say_duration(seconds))
        return f"Таймер на {say_duration(seconds)} запущен"

    @tool(
        "Создать напоминание: через сколько-то минут (in_minutes) или на точное время (at). "
        "Для «через 10 минут» используй in_minutes — текущего времени ты не знаешь"
    )
    async def set_reminder(
        self,
        text: Annotated[str, "О чём напомнить"],
        in_minutes: Annotated[int, "Через сколько минут, если сказано «через…»"] = 0,
        at: Annotated[str, "Локальное время ISO 8601, например 2026-10-06T18:30"] = "",
    ) -> str:
        now = time.time()
        if in_minutes > 0:
            due = now + in_minutes * 60
        elif at:
            try:
                due = datetime.fromisoformat(at).timestamp()
            except ValueError:
                return "Ошибка: at должно быть временем ISO 8601"
            if due <= now:
                return "Ошибка: это время уже прошло; для «через N минут» передай in_minutes"
        else:
            return "Ошибка: укажи in_minutes или at"
        await self._add("reminder", due, label=text)
        return f"Напоминание создано на {datetime.fromtimestamp(due):%d.%m %H:%M}"

    @tool("Список активных таймеров, будильников и напоминаний")
    async def list_timers(self) -> str:
        items = sorted(await self.storage.get(_KEY, []), key=lambda i: i["due"])
        now = time.time()
        return "; ".join(self._describe(i, now) for i in items) or "Нет активных"

    # -- internals ------------------------------------------------------------------------

    @staticmethod
    def _parse_reminder(request: str) -> tuple[float | None, str]:
        duration = parse_duration(request)
        if (
            duration is not None
            and duration.start > 0
            and duration.tokens[duration.start - 1] == "через"
        ):
            return time.time() + duration.value, duration.rest
        clock = parse_clock(request)
        if clock is not None:
            hour, minute = clock.value
            return next_clock(hour, minute).timestamp(), clock.rest
        if duration is not None:
            return time.time() + duration.value, duration.rest
        return None, request.strip()

    def _describe(self, item: dict[str, Any], now: float) -> str:
        left = say_duration(max(0.0, item["due"] - now))
        match item["kind"]:
            case "timer":
                return f"таймер на {item['label']}, осталось {left}"
            case "alarm":
                return f"будильник на {item['label']}"
            case _:
                return (
                    f"напоминание «{item['label']}» в {datetime.fromtimestamp(item['due']):%H:%M}"
                )

    async def _add(self, kind: Kind, due: float, *, label: str) -> dict[str, Any]:
        item = {"id": uuid.uuid4().hex[:8], "kind": kind, "due": due, "label": label}
        items: list[dict[str, Any]] = await self.storage.get(_KEY, [])
        items.append(item)
        await self.storage.set(_KEY, items)
        self._schedule(item)
        return item

    async def _remove(self, item_id: str) -> None:
        items: list[dict[str, Any]] = await self.storage.get(_KEY, [])
        await self.storage.set(_KEY, [i for i in items if i["id"] != item_id])
        task = self._scheduled.pop(item_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _schedule(self, item: dict[str, Any]) -> None:
        self._scheduled[item["id"]] = self.create_task(self._fire_at(item))

    async def _fire_at(self, item: dict[str, Any]) -> None:
        await asyncio.sleep(max(0.0, item["due"] - time.time()))
        await self._remove(item["id"])
        address = self.user_address.capitalize()
        match item["kind"]:
            case "timer":
                text = f"{address}, таймер на {item['label']} истёк."
            case "alarm":
                now = datetime.now()
                text = f"Доброе утро, {self.user_address}. Сейчас {now:%H:%M}. Пора вставать."
            case _:
                text = f"{address}, напоминаю: {item['label']}."
        await self.notify("Aion", text, "warning")
        await self.say(text, emotion="surprise")

    async def _announce_missed(self, items: list[dict[str, Any]]) -> None:
        await asyncio.sleep(1.0)
        labels = ", ".join(i["label"] for i in items if i["kind"] == "reminder")
        count = fmt_count(len(items), ("напоминание", "напоминания", "напоминаний"))
        text = f"Пока я {self.g('был выключен', 'была выключена')}, сработали {count}"
        await self.say(f"{text}: {labels}." if labels else f"{text}.")
