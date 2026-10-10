"""Voice calculator, unit and currency converter."""

from __future__ import annotations

from typing import Annotated

import httpx

from aion.sdk import Context, Plugin, command, tool

from .currency import Rates, find_currency, parse_currency, say_money
from .mathexpr import CalcError, evaluate, format_number, spoken_to_expression
from .units import parse_conversion, say_quantity

_ERRORS = (CalcError, ArithmeticError, ValueError, TypeError)


class Calc(Plugin):
    async def on_load(self) -> None:
        self.rates = Rates()

    async def _convert(self, ctx: Context, text: str) -> bool:
        """Units or currency; returns False when the phrase is neither."""
        conversion = parse_conversion(text)
        if conversion is not None:
            await ctx.say(
                f"{say_quantity(conversion.value, conversion.src)} — это "
                f"{say_quantity(conversion.result, conversion.dst)}."
            )
            return True
        money = parse_currency(text)
        if money is None:
            return False
        try:
            result = await self.rates.convert(money)
        except (httpx.HTTPError, KeyError):
            await ctx.say("Не удалось получить курс валют.")
            return True
        await ctx.say(
            f"{say_money(money.amount, money.src)} — это примерно {say_money(result, money.dst)}."
        )
        return True

    @command(["(сколько будет|посчитай|вычисли|подсчитай) {expression}"])
    async def calculate_cmd(self, ctx: Context, expression: str) -> bool:
        if await self._convert(ctx, expression):
            return True
        try:
            result = evaluate(spoken_to_expression(expression))
        except _ERRORS as e:
            self.log.debug("Не посчитал {!r}: {} — отдаю дальше", expression, e)
            return False
        await ctx.say(f"Будет {format_number(result)}.")
        return True

    @command(["(переведи|конвертируй) {request}", "сколько {request}"], priority=-1)
    async def convert_cmd(self, ctx: Context, request: str) -> bool:
        return await self._convert(ctx, request)

    @command(["[какой] курс {currency}", "сколько стоит {currency}"])
    async def rate_cmd(self, ctx: Context, currency: str) -> bool:
        code = find_currency(currency)
        if code is None or code == "RUB":
            return False
        try:
            rates = await self.rates.get()
        except httpx.HTTPError:
            await ctx.say("Не удалось получить курс валют.")
            return True
        await ctx.say(f"Курс ЦБ: {say_money(1, code)} — {say_money(rates[code], 'RUB')}.")
        return True

    @tool("Точно вычислить арифметическое выражение (+ - * / ** sqrt abs)")
    async def calculate(self, expression: Annotated[str, "Выражение, например (2+3)*4"]) -> str:
        try:
            return format_number(evaluate(spoken_to_expression(expression)))
        except _ERRORS as e:
            return f"Ошибка: {e}"

    @tool("Перевести сумму из одной валюты в другую по курсу ЦБ РФ")
    async def convert_currency(
        self,
        amount: float,
        from_currency: Annotated[str, "Код ISO, например USD"],
        to_currency: Annotated[str, "Код ISO, например RUB"],
    ) -> str:
        from .currency import CurrencyRequest

        try:
            req = CurrencyRequest(amount, from_currency.upper(), to_currency.upper())
            return f"{await self.rates.convert(req):.2f} {req.dst}"
        except (httpx.HTTPError, KeyError) as e:
            return f"Ошибка: {e}"
