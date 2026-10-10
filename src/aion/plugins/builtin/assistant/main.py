"""Core conversational commands: naming, help, repeat, stop."""

from __future__ import annotations

import random

from aion.sdk import Context, Plugin, command


class Assistant(Plugin):
    @command(
        [
            "(теперь|отныне) тебя зовут {name}",
            "(теперь|отныне) ты {name}",
            "я буду (звать|называть) тебя {name}",
            "твое имя {name}",
        ],
        priority=5,
    )
    async def rename(self, ctx: Context, name: str) -> None:
        """Сменить имя ассистента."""
        new_name = " ".join(w.capitalize() for w in name.split())
        store = self.host.store
        profile_id = store.config.assistant.profile
        store.update({"profiles": {profile_id: {"name": new_name, "aliases": [name.lower()]}}})
        await ctx.say(f"Как скажете. Отныне я — {new_name}.", emotion="joy")

    @command(["(называй меня|обращайся ко мне|зови меня) {address}"], priority=5)
    async def set_address(self, ctx: Context, address: str) -> None:
        """Сменить обращение к пользователю."""
        self.host.store.update({"assistant": {"user_address": address}})
        await ctx.say(f"Хорошо, {address}.")

    @command(["как тебя зовут", "кто ты", "представься"])
    async def who(self, ctx: Context) -> None:
        await ctx.say(f"Меня зовут {ctx.assistant_name}. Я ваш персональный ассистент.")

    @command(["что ты умеешь", "помощь", "какие у тебя команды", "что ты можешь"])
    async def help(self, ctx: Context) -> None:
        """Рассказать о возможностях."""
        titles = [
            r.manifest.display_name.lower()
            for r in self.host.plugins.loaded()
            if r.manifest and r.name != self.name and r.commands
        ]
        skills = ", ".join(titles) if titles else "пока немного"
        tail = (
            " А на остальные вопросы отвечу с помощью языковой модели."
            if self.llm.available
            else ""
        )
        await ctx.say(f"Мои навыки: {skills}.{tail}")

    @command(["повтори", "повтори еще раз", "что ты сказал", "не расслышал"])
    async def repeat(self, ctx: Context) -> None:
        last = self.host.dialog.last_reply
        await ctx.say(last or ctx.g("Я пока ничего не говорил.", "Я пока ничего не говорила."))

    @command(["(стоп|хватит|замолчи|тихо|отмена|помолчи)"], priority=10)
    async def stop(self, ctx: Context) -> None:
        """Остановиться (речь уже прервана самим фактом новой фразы)."""

    @command(["спасибо", "благодарю", "спасибо большое"])
    async def thanks(self, ctx: Context) -> None:
        replies = [
            f"Всегда к вашим услугам, {ctx.user_address}.",
            ctx.g("Рад помочь.", "Рада помочь."),
            f"Не за что, {ctx.user_address}.",
        ]
        await ctx.say(random.choice(replies))
