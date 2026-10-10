"""Long-term memory: facts about the user and the diary of past conversations."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated

from aion.llm.episodes import EpisodeStore, say_date
from aion.llm.memory import FactStore
from aion.sdk import Context, Plugin, command, tool

# Facts worth keeping even when the model forgets to call its memory tool.
_SELF_FACTS = (
    (re.compile(r"\bменя зовут ([А-ЯЁA-Z][\w-]+)", re.IGNORECASE), "Пользователя зовут {0}"),
    (re.compile(r"\bмне (\d{1,3}) (?:год|года|лет)\b", re.IGNORECASE), "Пользователю {0} лет"),
)


class Memory(Plugin):
    async def on_utterance(self, ctx: Context) -> bool:
        """Quietly remember the user's name/age, then let the phrase continue as usual."""
        for pattern, template in _SELF_FACTS:
            if m := pattern.search(ctx.text):
                value = m.group(1)
                value = value[:1].upper() + value[1:]
                if await self.facts.add(template.format(value)):
                    self.log.info("Запомнила: {}", template.format(value))
        return False

    @property
    def facts(self) -> FactStore:
        return FactStore(self.host.db)

    @property
    def diary(self) -> EpisodeStore:
        return self.host.diary.store

    @command(["запомни [что] {fact}"], priority=3)
    async def remember(self, ctx: Context, fact: str) -> None:
        added = await self.facts.add(fact)
        await ctx.say(ctx.g("Запомнил.", "Запомнила.") if added else "Я это уже знаю.")

    @command(["что ты (обо мне|про меня) (знаешь|помнишь)", "что ты помнишь"])
    async def recall(self, ctx: Context) -> None:
        facts = await self.facts.all()
        if not facts:
            await ctx.say("Пока ничего. Скажите «запомни, что…», и я запомню.")
            return
        await ctx.say("Я знаю, что " + "; ".join(facts[:10]) + ".")

    @command(["забудь [что] {query}"], priority=3)
    async def forget(self, ctx: Context, query: str) -> None:
        if query in {"все", "всё", "все обо мне", "всё обо мне"}:
            if await ctx.confirm("Стереть всю память о вас?"):
                await self.forget_all(ctx)
            else:
                await ctx.say("Отменено.")
            return
        n = await self.facts.forget(query)
        await ctx.say(ctx.g("Забыл.", "Забыла.") if n else "Такого в памяти нет.")

    @command(
        ["забудь все обо мне", "очисти память"],
        dangerous=True,
        description="стереть всю память о вас",
    )
    async def forget_all(self, ctx: Context) -> None:
        await self.facts.clear()
        await self.diary.clear()
        if self.host.brain is not None:
            self.host.brain.memory.clear()
        await ctx.say("Память очищена.")

    @command(["(начни|давай начнем) (новый|сначала) разговор", "забудь [наш] разговор"], priority=5)
    async def new_conversation(self, ctx: Context) -> None:
        if self.host.brain is not None:
            self.host.brain.memory.clear()
        await ctx.say("Начнём с чистого листа.")

    @tool("Запомнить важный факт о пользователе надолго (имя, предпочтения, близкие, планы)")
    async def remember_fact(
        self, fact: Annotated[str, "Факт от третьего лица, например «пьёт кофе без сахара»"]
    ) -> str:
        return "Запомнено" if await self.facts.add(fact) else "Уже известно"

    @tool("Забыть факты о пользователе, содержащие строку")
    async def forget_fact(self, query: Annotated[str, "Подстрока факта"]) -> str:
        n = await self.facts.forget(query)
        return f"Удалено фактов: {n}"

    @tool(
        "Вспомнить прошлые разговоры с пользователем из дневника: по теме или за день. "
        "Без параметров — последние разговоры"
    )
    async def recall_conversations(
        self,
        query: Annotated[str, "О чём был разговор, например «отпуск» или «собеседование»"] = "",
        days_ago: Annotated[int | None, "За какой день: 0 — сегодня, 1 — вчера"] = None,
    ) -> str:
        today = date.today()
        if days_ago is not None:
            episodes = await self.diary.on_day(today - timedelta(days=days_ago))
        elif query:
            episodes = await self.diary.search(query)
        else:
            episodes = await self.diary.recent(5)
        if not episodes:
            return "В дневнике ничего такого нет"
        return "\n".join(
            f"{say_date(date.fromtimestamp(e.started), today)}: {e.summary}" for e in episodes
        )
