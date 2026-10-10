"""Example user plugin: coin flip, dice, a clarifying question and persistent storage."""

import random
from typing import Annotated

from aion.sdk import Context, Plugin, command, fmt_count, tool


class Coin(Plugin):
    @command(["(подбрось|брось|кинь) монетку", "орел или решка"])
    async def flip(self, ctx: Context) -> None:
        side = random.choice(["Орёл", "Решка"])
        count = await ctx.storage.get("flips", 0) + 1
        await ctx.storage.set("flips", count)
        await ctx.say(f"{side}! Это был {count}-й бросок.")

    @command(["(брось|кинь) кубик", "(брось|кинь) {n} (кубика|кубиков|кубик)"])
    async def dice(self, ctx: Context, n: int = 1) -> None:
        sides = int(ctx.config["dice_sides"])
        rolls = [random.randint(1, sides) for _ in range(max(1, min(n, 10)))]
        if len(rolls) == 1:
            await ctx.say(f"Выпало {rolls[0]}.")
        else:
            await ctx.say(f"Выпало {', '.join(map(str, rolls))}. Сумма {sum(rolls)}.")

    @command(["выбери (за меня|случайно)", "помоги выбрать"])
    async def choose(self, ctx: Context) -> None:
        answer = await ctx.ask("Из каких вариантов выбрать? Перечислите через «или».")
        if not answer:
            await ctx.say("Ладно, в другой раз.")
            return
        options = [o.strip() for o in answer.replace(",", " или ").split(" или ") if o.strip()]
        if len(options) < 2:
            await ctx.say("Нужно хотя бы два варианта.")
            return
        await ctx.say(f"Я выбираю: {random.choice(options)}.")

    @command(["сколько раз ты бросал монетку"])
    async def stats(self, ctx: Context) -> None:
        count = await ctx.storage.get("flips", 0)
        await ctx.say(f"Я подбросил монетку {fmt_count(count, ('раз', 'раза', 'раз'))}.")

    @tool("Бросить игральные кубики")
    async def roll_dice(self, count: Annotated[int, "Сколько кубиков"] = 1) -> str:
        sides = int(self.config["dice_sides"])
        return str([random.randint(1, sides) for _ in range(max(1, min(count, 10)))])
