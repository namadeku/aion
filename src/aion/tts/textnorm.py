"""Text normalization for speech synthesis: numbers, times, units and symbols -> Russian words."""

from __future__ import annotations

import re

from num2words import num2words

from aion.text.plural import plural

_FEMININE_STEMS = (
    "минут", "секунд", "недел", "мил", "тонн", "унци", "единиц", "штук",
    "копе", "гривн", "строк", "задач", "заметк", "запис", "ночь", "ночи",
)  # fmt: skip
_FRACTIONS = {1: "десят", 2: "сот", 3: "тысячн"}

_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_DEGREES = re.compile(r"([+\-−]?)(\d+(?:[.,]\d+)?)\s*°\s*([CcСсFfF])?")
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_MS = ("метр в секунду", "метра в секунду", "метров в секунду")
_KMH = ("километр в час", "километра в час", "километров в час")
_UNITS = (
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*м/с\b"), _MS),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*км/ч\b"), _KMH),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*км\b"), ("километр", "километра", "километров")),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*кг\b"), ("килограмм", "килограмма", "килограммов")),
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*мм\b"), ("миллиметр", "миллиметра", "миллиметров")),
)  # fmt: skip
_NUMBER = re.compile(r"(?<![\w.,])([+\-−]?)(\d+)(?:[.,](\d+))?(?![\w])")
_SYMBOLS = {"№": " номер ", "&": " и ", "$": " долларов ", "€": " евро ", "₽": " рублей "}


def _feminize(words: str, *, accusative: bool = False) -> str:
    parts = words.split()
    if parts and parts[-1] == "один":
        parts[-1] = "одну" if accusative else "одна"
    elif parts and parts[-1] == "два":
        parts[-1] = "две"
    return " ".join(parts)


def number_words(
    integer: str,
    fraction: str | None = None,
    *,
    feminine: bool = False,
    accusative: bool = False,
) -> str:
    whole = num2words(int(integer), lang="ru")
    if not fraction:
        return _feminize(whole, accusative=accusative) if feminine else whole
    fraction = fraction[:3].rstrip("0") or "0"
    if fraction == "0":
        return _feminize(whole) if feminine else whole
    n_int, n_frac = int(integer), int(fraction)
    whole = _feminize(whole) + " " + plural(n_int, ("целая", "целых", "целых"))
    stem = _FRACTIONS[len(fraction)]
    frac_word = stem + ("ая" if n_frac % 10 == 1 and n_frac % 100 != 11 else "ых")
    return f"{whole} {_feminize(num2words(n_frac, lang='ru'))} {frac_word}"


def _value(integer: str, fraction: str | None) -> float:
    return float(f"{integer}.{fraction}") if fraction else float(integer)


def _say_quantity(match_number: str, forms: tuple[str, str, str], *, feminine: bool = False) -> str:
    integer, _, fraction = match_number.replace(",", ".").partition(".")
    words = number_words(integer, fraction or None, feminine=feminine)
    return f"{words} {plural(_value(integer, fraction or None), forms)}"


def _time(m: re.Match[str]) -> str:
    hours, minutes = int(m.group(1)), int(m.group(2))
    h = num2words(hours, lang="ru")
    if minutes == 0:
        return f"{h} {plural(hours, ('час', 'часа', 'часов'))}"
    mm = _feminize(num2words(minutes, lang="ru"))
    return f"{h} {'ноль ' + mm if minutes < 10 else mm}"


def _degrees(m: re.Match[str]) -> str:
    sign, number, scale = m.group(1), m.group(2), (m.group(3) or "").lower()
    prefix = {"+": "плюс ", "-": "минус ", "−": "минус "}.get(sign, "")
    text = _say_quantity(number, ("градус", "градуса", "градусов"))
    suffix = {"c": " Цельсия", "с": " Цельсия", "f": " по Фаренгейту"}.get(scale, "")
    return f"{prefix}{text}{suffix}"


def _number(m: re.Match[str]) -> str:
    sign, integer, fraction = m.group(1), m.group(2), m.group(3)
    rest = m.string[m.end() :].lstrip().lower()
    feminine = rest.startswith(_FEMININE_STEMS)
    noun = rest.split(maxsplit=1)[0] if rest else ""
    accusative = noun.rstrip(".,!?;:").endswith(("у", "ю"))
    words = number_words(integer, fraction, feminine=feminine, accusative=accusative)
    return ("минус " if sign in {"-", "−"} else "плюс " if sign == "+" else "") + words


def normalize_for_speech(text: str) -> str:
    """Make text pronounceable by TTS engines that do not read digits and symbols."""
    for symbol, words in _SYMBOLS.items():
        text = text.replace(symbol, words)
    text = _TIME.sub(_time, text)
    text = _DEGREES.sub(_degrees, text)
    text = _PERCENT.sub(
        lambda m: _say_quantity(m.group(1), ("процент", "процента", "процентов")), text
    )
    for pattern, forms in _UNITS:
        text = pattern.sub(lambda m, f=forms: _say_quantity(m.group(1), f), text)
    text = _NUMBER.sub(_number, text)
    text = re.sub(r"\s*—\s*", ", ", text).replace("«", "").replace("»", "")
    return re.sub(r"\s+", " ", text).strip()
