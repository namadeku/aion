"""Weather via Open-Meteo (no API key)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

import httpx

from aion.sdk import Context, Plugin, command, tool

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "ледяная морось", 61: "небольшой дождь", 63: "дождь",
    65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь", 71: "небольшой снег",
    73: "снег", 75: "сильный снег", 77: "снежная крупа", 80: "ливень", 81: "ливни",
    82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад", 95: "гроза",
    96: "гроза с градом", 99: "сильная гроза с градом",
}  # fmt: skip

Day = Literal["today", "tomorrow"]


def city_candidates(raw: str) -> list[str]:
    """Undo the Russian prepositional case: "москве" -> "москва", "петербурге" -> "петербург"."""
    word = raw.strip().removeprefix("городе ").removeprefix("город ")
    out = [word]
    for ending, replacements in (
        ("ске", ("ск",)),
        ("ве", ("ва",)),
        ("е", ("", "а", "я", "ь")),
        ("и", ("ь", "и", "а")),
        ("у", ("а", "")),
    ):
        if word.endswith(ending):
            stem = word[: -len(ending)]
            out += [stem + r for r in replacements]
    return list(dict.fromkeys(c for c in out if len(c) > 1))


def signed(t: float) -> str:
    value = round(t)
    return f"+{value}°" if value > 0 else f"{value}°"


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float


class Weather(Plugin):
    timeout = 10.0

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def geocode(self, city: str) -> Place | None:
        for candidate in city_candidates(city.lower()):
            data = await self._get(
                GEOCODE_URL, {"name": candidate, "count": 1, "language": "ru", "format": "json"}
            )
            if results := data.get("results"):
                r = results[0]
                return Place(r["name"], r["latitude"], r["longitude"])
        return None

    async def forecast(self, place: Place) -> dict[str, Any]:
        return await self._get(
            FORECAST_URL,
            {
                "latitude": place.latitude,
                "longitude": place.longitude,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                "precipitation_probability_max",
                "wind_speed_unit": "ms",
                "timezone": "auto",
                "forecast_days": 2,
            },
        )

    async def report(self, city: str | None, day: Day = "today") -> str:
        city = city or str(self.config["city"])
        try:
            place = await self.geocode(city)
            if place is None:
                return f"Не получилось найти город {city}."
            data = await self.forecast(place)
        except httpx.HTTPError as e:
            self.log.warning("Open-Meteo недоступен: {}", e)
            return "Сервис погоды сейчас недоступен."
        return format_report(place.name, data, day)

    # -- commands -------------------------------------------------------------------------

    @command(["[какая] [сейчас] погода", "что с погодой", "(скажи|подскажи) погоду"])
    async def now(self, ctx: Context) -> None:
        await ctx.say(await self.report(None))

    @command(["[какая] [сейчас] погода в {city}", "погода (в городе|для) {city}"])
    async def in_city(self, ctx: Context, city: str) -> None:
        if city.startswith(("завтра", "на завтра")):
            await ctx.say(await self.report(None, "tomorrow"))
            return
        await ctx.say(await self.report(city))

    @command(["[какая] погода [будет] (завтра|на завтра)", "что (с погодой|по погоде) на завтра"])
    async def tomorrow(self, ctx: Context) -> None:
        await ctx.say(await self.report(None, "tomorrow"))

    @command(["[какая] погода [будет] (завтра|на завтра) в {city}"])
    async def tomorrow_in_city(self, ctx: Context, city: str) -> None:
        await ctx.say(await self.report(city, "tomorrow"))

    @command(["(нужен ли|брать ли) [мне] зонт", "будет ли (дождь|снег) [сегодня]"])
    async def umbrella(self, ctx: Context) -> None:
        try:
            place = await self.geocode(str(self.config["city"]))
            data = await self.forecast(place) if place else None
        except httpx.HTTPError:
            data = None
        if not data:
            await ctx.say("Не удалось узнать прогноз.")
            return
        chance = data["daily"]["precipitation_probability_max"][0] or 0
        if chance >= 50:
            await ctx.say(f"Вероятность осадков {chance}%. Зонт лучше взять.")
        else:
            await ctx.say(f"Вероятность осадков {chance}%. Можно обойтись без зонта.")

    # -- LLM tool -------------------------------------------------------------------------

    @tool("Получить текущую погоду или прогноз на завтра для города")
    async def get_weather(
        self,
        city: Annotated[str | None, "Город; пусто — город пользователя по умолчанию"] = None,
        day: Annotated[Day, "today или tomorrow"] = "today",
    ) -> str:
        return await self.report(city, day)


def format_report(name: str, data: dict[str, Any], day: Day) -> str:
    if day == "today":
        cur = data["current"]
        sky = WMO.get(int(cur["weather_code"]), "без осадков")
        text = (
            f"{name}: сейчас {signed(cur['temperature_2m'])}, {sky}, "
            f"ощущается как {signed(cur['apparent_temperature'])}. "
            f"Ветер {round(cur['wind_speed_10m'])} м/с."
        )
        return text
    daily = data["daily"]
    sky = WMO.get(int(daily["weather_code"][1]), "без осадков")
    chance = daily["precipitation_probability_max"][1]
    rain = f" Вероятность осадков {chance}%." if chance else ""
    return (
        f"{name}, завтра: {sky}, от {signed(daily['temperature_2m_min'][1])} "
        f"до {signed(daily['temperature_2m_max'][1])}.{rain}"
    )
