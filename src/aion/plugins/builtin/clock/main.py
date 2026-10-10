"""Current time, date and weekday."""

from __future__ import annotations

from datetime import datetime

from aion.sdk import Context, Plugin, command, tool

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def say_date(dt: datetime) -> str:
    return f"{dt.day} {MONTHS[dt.month - 1]}"


class Clock(Plugin):
    @command(["который час", "сколько времени", "(скажи|подскажи) время", "сколько сейчас времени"])
    async def time(self, ctx: Context) -> None:
        now = datetime.now()
        await ctx.say(f"Сейчас {now:%H:%M}.")

    @command(
        [
            "какое сегодня число",
            "какое число",
            "какая сегодня дата",
            "какой сегодня день",
            "(скажи|подскажи) дату",
        ]
    )
    async def date(self, ctx: Context) -> None:
        now = datetime.now()
        await ctx.say(f"Сегодня {WEEKDAYS[now.weekday()]}, {say_date(now)}.")

    @command(["какой (сегодня|сейчас) день недели", "какой день недели"])
    async def weekday(self, ctx: Context) -> None:
        await ctx.say(f"Сегодня {WEEKDAYS[datetime.now().weekday()]}.")

    @command(["какой (сейчас|сегодня) год", "какой год"])
    async def year(self, ctx: Context) -> None:
        await ctx.say(f"Сейчас {datetime.now().year} год.")

    @tool("Текущие локальные дата, время и день недели")
    async def current_datetime(self) -> str:
        now = datetime.now().astimezone()
        return f"{now:%Y-%m-%d %H:%M} ({WEEKDAYS[now.weekday()]}), часовой пояс {now:%Z %z}"
