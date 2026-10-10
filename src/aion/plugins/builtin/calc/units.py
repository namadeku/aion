"""Unit conversion for spoken requests: "5 километров в милях"."""

from __future__ import annotations

import re
from dataclasses import dataclass

from aion.text import normalize
from aion.text.plural import plural


@dataclass(frozen=True)
class Unit:
    key: str
    dimension: str
    factor: float  # to the base unit of its dimension
    stems: tuple[str, ...]
    forms: tuple[str, str, str]
    abbr: tuple[str, ...] = ()


UNITS = (
    Unit("mm", "length", 0.001, ("миллиметр",), ("миллиметр", "миллиметра", "миллиметров"), ("мм",)),
    Unit("cm", "length", 0.01, ("сантиметр",), ("сантиметр", "сантиметра", "сантиметров"), ("см",)),
    Unit("km", "length", 1000, ("километр",), ("километр", "километра", "километров"), ("км",)),
    Unit("m", "length", 1, ("метр",), ("метр", "метра", "метров"), ("м",)),
    Unit("nmi", "length", 1852, ("морск",), ("морская миля", "морские мили", "морских миль")),
    Unit("mi", "length", 1609.344, ("мил",), ("миля", "мили", "миль")),
    Unit("ft", "length", 0.3048, ("фут",), ("фут", "фута", "футов")),
    Unit("in", "length", 0.0254, ("дюйм",), ("дюйм", "дюйма", "дюймов")),
    Unit("yd", "length", 0.9144, ("ярд",), ("ярд", "ярда", "ярдов")),
    Unit("mg", "mass", 1e-6, ("миллиграм",), ("миллиграмм", "миллиграмма", "миллиграммов"), ("мг",)),
    Unit("kg", "mass", 1, ("килограм", "кило"), ("килограмм", "килограмма", "килограммов"), ("кг",)),
    Unit("g", "mass", 0.001, ("грам",), ("грамм", "грамма", "граммов"), ("г", "гр")),
    Unit("t", "mass", 1000, ("тонн",), ("тонна", "тонны", "тонн"), ("т",)),
    Unit("lb", "mass", 0.45359237, ("фунт",), ("фунт", "фунта", "фунтов")),
    Unit("oz", "mass", 0.028349523125, ("унци",), ("унция", "унции", "унций")),
    Unit("ml", "volume", 0.001, ("миллилитр",), ("миллилитр", "миллилитра", "миллилитров"), ("мл",)),
    Unit("l", "volume", 1, ("литр",), ("литр", "литра", "литров"), ("л",)),
    Unit("gal", "volume", 3.785411784, ("галлон",), ("галлон", "галлона", "галлонов")),
    Unit("s", "time", 1, ("секунд",), ("секунда", "секунды", "секунд"), ("сек", "с")),
    Unit("min", "time", 60, ("минут",), ("минута", "минуты", "минут"), ("мин",)),
    Unit("h", "time", 3600, ("час",), ("час", "часа", "часов"), ("ч",)),
    Unit("d", "time", 86400, ("сут", "дн", "день"), ("день", "дня", "дней")),
    Unit("wk", "time", 604800, ("недел",), ("неделя", "недели", "недель")),
    Unit("C", "temp", 1, ("цельси",), ("градус Цельсия", "градуса Цельсия", "градусов Цельсия")),
    Unit("F", "temp", 1, ("фаренгейт",), ("градус Фаренгейта", "градуса Фаренгейта", "градусов Фаренгейта")),
    Unit("K", "temp", 1, ("кельвин",), ("кельвин", "кельвина", "кельвинов")),
    Unit("kmh", "speed", 1 / 3.6, ("километров в час", "км ч"), ("километр в час", "километра в час", "километров в час")),
    Unit("ms", "speed", 1, ("метров в секунду", "м с"), ("метр в секунду", "метра в секунду", "метров в секунду")),
)  # fmt: skip

_REQUEST = re.compile(r"^(?P<n>-?\d+(?:[.,]\d+)?)\s+(?P<src>.+?)\s+(?:в|во|на)\s+(?P<dst>.+)$")
_FILLER = re.compile(r"\b(сколько|будет|переведи|конвертируй|это|градус\w*)\b")


def find_unit(words: str) -> Unit | None:
    words = words.strip()
    for unit in UNITS:
        if words in unit.abbr:
            return unit
    for candidate in sorted(UNITS, key=lambda u: -max(len(s) for s in u.stems)):
        if any(words.startswith(stem) for stem in candidate.stems):
            return candidate
    return None


def _to_celsius(value: float, unit: Unit) -> float:
    return {"C": value, "F": (value - 32) * 5 / 9, "K": value - 273.15}[unit.key]


def _from_celsius(value: float, unit: Unit) -> float:
    return {"C": value, "F": value * 9 / 5 + 32, "K": value + 273.15}[unit.key]


@dataclass(frozen=True)
class Conversion:
    value: float
    src: Unit
    result: float
    dst: Unit


def parse_conversion(text: str) -> Conversion | None:
    norm = " ".join(_FILLER.sub(" ", normalize(text)).split())
    m = _REQUEST.match(norm)
    if not m:
        return None
    src, dst = find_unit(m.group("src")), find_unit(m.group("dst"))
    if src is None or dst is None or src.dimension != dst.dimension:
        return None
    value = float(m.group("n").replace(",", "."))
    if src.dimension == "temp":
        result = _from_celsius(_to_celsius(value, src), dst)
    else:
        result = value * src.factor / dst.factor
    return Conversion(value, src, result, dst)


def say_quantity(value: float, unit: Unit) -> str:
    rounded = round(value, 3 if abs(value) < 10 else 2)
    number = str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}".replace(".", ",")
    return f"{number} {plural(rounded, unit.forms)}"
