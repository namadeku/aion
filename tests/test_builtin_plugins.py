from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.text.timeparse import parse_clock, parse_duration
from tests.helpers import load_builtin_module, say, settle

mathexpr = load_builtin_module("calc", "mathexpr")
units = load_builtin_module("calc", "units")
weather = load_builtin_module("weather", "main")


# -- time parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds", "rest"),
    [
        ("5 минут", 300, ""),
        ("через двадцать пять минут выключить чайник", 1500, "выключить чайник"),
        ("полчаса", 1800, ""),
        ("полтора часа", 5400, ""),
        ("через час позвонить маме", 3600, "позвонить маме"),
        ("1 час и 30 минут", 5400, ""),
        ("выключить плиту через 10 секунд", 10, "выключить плиту"),
    ],
)
def test_parse_duration(text: str, seconds: float, rest: str) -> None:
    parsed = parse_duration(text)
    assert parsed is not None
    assert parsed.value == seconds
    assert parsed.rest == rest


def test_parse_duration_none() -> None:
    assert parse_duration("позвонить маме") is None


@pytest.mark.parametrize(
    ("text", "clock", "rest"),
    [
        ("в 7 утра", (7, 0), ""),
        ("в 7 вечера позвонить", (19, 0), "позвонить"),
        ("позвонить маме в 18:30", (18, 30), "позвонить маме"),
        ("на 6 часов 15 минут", (6, 15), ""),
        ("в 12 ночи", (0, 0), ""),
    ],
)
def test_parse_clock(text: str, clock: tuple[int, int], rest: str) -> None:
    parsed = parse_clock(text)
    assert parsed is not None
    assert parsed.value == clock
    assert parsed.rest == rest


# -- calculator ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spoken", "result"),
    [
        ("два плюс два", 4),
        ("2 плюс 2 умножить на 3", 8),
        ("сто разделить на восемь", 12.5),
        ("три в квадрате", 9),
        ("корень из 144", 12),
        ("15 процентов от 200", 30),
        ("2,5 плюс 0,5", 3),
        ("минус 5 плюс 2", -3),
    ],
)
def test_calc(spoken: str, result: float) -> None:
    assert mathexpr.evaluate(mathexpr.spoken_to_expression(spoken)) == pytest.approx(result)


@pytest.mark.parametrize(
    "expr", ["__import__('os')", "2 ** 10000", "1 / 0", "open('x')", "().__class__"]
)
def test_calc_is_safe(expr: str) -> None:
    with pytest.raises(mathexpr.CalcError):
        mathexpr.evaluate(expr)


def test_format_number() -> None:
    assert mathexpr.format_number(4.0) == "4"
    assert mathexpr.format_number(12.5) == "12,5"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 километров в милях", "5 километров — 3,107 мили"),
        ("100 градусов цельсия в фаренгейтах", "100 градусов Цельсия — 212 градусов Фаренгейта"),
        ("2 фунта в килограммах", "2 фунта — 0,907 килограмма"),
        ("90 минут в часах", "90 минут — 1,5 часа"),
    ],
)
def test_units(text: str, expected: str) -> None:
    conv = units.parse_conversion(text)
    assert conv is not None
    got = (
        f"{units.say_quantity(conv.value, conv.src)} — {units.say_quantity(conv.result, conv.dst)}"
    )
    assert got == expected


def test_units_reject_mismatch() -> None:
    assert units.parse_conversion("5 километров в литрах") is None


async def test_calc_commands(app: Aion) -> None:
    assert await say(app, "сколько будет два плюс два умножить на три") == ["Будет 8."]
    assert await say(app, "переведи 10 километров в мили") == ["10 километров — это 6,214 мили."]


async def test_calc_declines_to_fallback(app: Aion) -> None:
    seen: list[str] = []

    async def fallback(turn: Any) -> None:
        seen.append(turn.text)

    app.dialog.fallback = fallback
    await say(app, "сколько лет луне")
    assert seen == ["сколько лет луне"]


# -- weather ---------------------------------------------------------------------------


def test_city_candidates() -> None:
    assert "москва" in weather.city_candidates("москве")
    assert "санкт-петербург" in weather.city_candidates("санкт-петербурге")
    assert "новосибирск" in weather.city_candidates("новосибирске")
    assert "тверь" in weather.city_candidates("твери")


def test_format_report() -> None:
    data = {
        "current": {
            "temperature_2m": 4.6,
            "apparent_temperature": -0.4,
            "weather_code": 3,
            "wind_speed_10m": 4.2,
        },
        "daily": {
            "temperature_2m_min": [1, -2.2],
            "temperature_2m_max": [6, 3.1],
            "weather_code": [3, 71],
            "precipitation_probability_max": [10, 80],
        },
    }
    assert weather.format_report("Москва", data, "today") == (
        "Москва: сейчас +5°, пасмурно, ощущается как 0°. Ветер 4 м/с."
    )
    assert weather.format_report("Москва", data, "tomorrow") == (
        "Москва, завтра: небольшой снег, от -2° до +3°. Вероятность осадков 80%."
    )


async def test_weather_command_with_mocked_api(app: Aion, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = app.plugins.instance("weather")
    assert inst is not None
    calls: list[str] = []

    async def fake_get(url: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(params.get("name", "forecast"))
        if "geocoding" in url:
            if params["name"] == "лондон":
                return {"results": [{"name": "Лондон", "latitude": 51.5, "longitude": -0.1}]}
            return {}
        return {
            "current": {
                "temperature_2m": 12,
                "apparent_temperature": 10,
                "weather_code": 61,
                "wind_speed_10m": 6,
            }
        }

    monkeypatch.setattr(inst, "_get", fake_get)
    replies = await say(app, "какая погода в Лондоне")
    assert replies == ["Лондон: сейчас +12°, небольшой дождь, ощущается как +10°. Ветер 6 м/с."]
    assert calls[:2] == ["лондоне", "лондон"]


# -- clock & assistant -----------------------------------------------------------------


async def test_clock(app: Aion) -> None:
    (reply,) = await say(app, "который час")
    assert reply == f"Сейчас {datetime.now():%H:%M}."


async def test_rename_applies_immediately(app: Aion) -> None:
    assert await say(app, "теперь тебя зовут пятница") == ["Как скажете. Отныне я — Пятница."]
    assert app.config.profile.name == "Пятница"
    assert "пятница" in app.config.profile.wake_names()
    assert await say(app, "как тебя зовут") == ["Меня зовут Пятница. Я ваш персональный ассистент."]


async def test_repeat_and_help(app: Aion) -> None:
    await say(app, "как тебя зовут")
    assert await say(app, "повтори") == ["Меня зовут Aion. Я ваш персональный ассистент."]
    (help_text,) = await say(app, "что ты умеешь")
    assert "погода" in help_text


# -- timers ----------------------------------------------------------------------------


async def test_timer_fires(app: Aion) -> None:
    assert await say(app, "поставь таймер на 0.2 секунды") == ["Таймер на 0 секунд запущен."]
    assert isinstance(app.speech, NullOutput)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not any("истёк" in s for s in app.speech.spoken):
        await settle(app, 5)
    assert any("истёк" in s for s in app.speech.spoken)


async def test_reminder_survives_restart(app_store: ConfigStore) -> None:
    def make() -> Aion:
        return Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)

    async with make() as aion:
        (reply,) = await say(aion, "напомни мне через 10 минут выключить чайник")
        assert reply.endswith(": выключить чайник.")
    async with make() as aion:
        (status,) = await say(aion, "какие у меня напоминания")
        assert "выключить чайник" in status
        assert await say(aion, "отмени напоминания") == ["Отменено."]
        assert await say(aion, "какие у меня напоминания") == [
            "Активных таймеров и напоминаний нет."
        ]


async def test_alarm(app: Aion) -> None:
    (reply,) = await say(app, "разбуди меня в 7 утра")
    assert reply.startswith("Будильник установлен на 7:00")


async def test_reminder_tool_relative_and_past_time(app: Aion) -> None:
    import time
    from datetime import datetime, timedelta

    tool = next(t for t in app.plugins.tools() if t.qualified_name == "timers__set_reminder")
    result = await tool.invoke({"text": "выключить чайник", "in_minutes": 10})
    expected = datetime.fromtimestamp(time.time() + 600)
    assert result == f"Напоминание создано на {expected:%d.%m %H:%M}"
    past = (datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes")
    assert (await tool.invoke({"text": "x", "at": past})).startswith("Ошибка: это время уже прошло")
    assert (await tool.invoke({"text": "x"})).startswith("Ошибка")
