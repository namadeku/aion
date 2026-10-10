"""Parsing spoken durations ("через 5 минут"), clock times ("в 7 вечера") and days ("в пятницу")."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from aion.text.normalize import normalize

_UNIT_SECONDS: dict[str, float] = {}
for _words, _secs in (
    (("секунда", "секунду", "секунды", "секунд", "сек", "second", "seconds", "sec"), 1),
    (("минута", "минуту", "минуты", "минут", "мин", "minute", "minutes", "min"), 60),
    (("час", "часа", "часов", "ч", "hour", "hours"), 3600),
    (("день", "дня", "дней", "сутки", "суток", "day", "days"), 86400),
    (("неделя", "неделю", "недели", "недель", "week", "weeks"), 604800),
):
    for _w in _words:
        _UNIT_SECONDS[_w] = _secs

_SPECIAL = {"полчаса": 1800.0, "полминуты": 30.0, "полдня": 43200.0}
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_SKIP_BEFORE = {"через", "на", "в", "течение", "за"}
_JOINERS = {"и"}


def _num(token: str) -> float:
    return float(token.replace(",", "."))


@dataclass(frozen=True)
class Parsed[T]:
    value: T
    start: int  # token index where the expression starts
    end: int  # token index after the expression
    tokens: tuple[str, ...]

    @property
    def rest(self) -> str:
        """Phrase without the parsed expression (and its leading preposition)."""
        before = list(self.tokens[: self.start])
        if before and before[-1] in _SKIP_BEFORE:
            before.pop()
        return " ".join([*before, *self.tokens[self.end :]]).strip()


def parse_duration(text: str) -> Parsed[float] | None:
    """Find the first duration in ``text``; value is in seconds."""
    tokens = tuple(normalize(text).split())
    i = 0
    while i < len(tokens):
        total, j = _duration_at(tokens, i)
        if total > 0:
            return Parsed(total, i, j, tokens)
        i += 1
    return None


def _duration_at(tokens: tuple[str, ...], i: int) -> tuple[float, int]:
    total = 0.0
    j = i
    while j < len(tokens):
        tok = tokens[j]
        if tok in _SPECIAL:
            total += _SPECIAL[tok]
            j += 1
        elif _NUMBER.match(tok) and j + 1 < len(tokens) and tokens[j + 1] in _UNIT_SECONDS:
            total += _num(tok) * _UNIT_SECONDS[tokens[j + 1]]
            j += 2
        elif tok in _UNIT_SECONDS and tok not in {"ч", "мин", "сек"} and j == i:
            # bare unit: "через час", "через минуту"
            total += _UNIT_SECONDS[tok]
            j += 1
        elif tok in _JOINERS and total > 0 and j + 1 < len(tokens):
            nxt_total, nxt_j = _duration_at(tokens, j + 1)
            if nxt_total <= 0:
                break
            total += nxt_total
            j = nxt_j
        else:
            break
    return total, j


_HOUR_WORDS = {"час", "часа", "часов", "ч"}
_MINUTE_WORDS = {"минута", "минуту", "минуты", "минут", "мин"}
_DAYPART = {"утра": 0, "ночи": 0, "дня": 12, "вечера": 12}


def parse_clock(text: str) -> Parsed[tuple[int, int]] | None:
    """Find a clock time: "в 18 30", "в 7 утра", "в 7 часов 15 минут", "на 6:45"."""
    tokens = tuple(normalize(text.replace(":", " ")).split())
    for i, tok in enumerate(tokens):
        if not tok.isdigit():
            continue
        if i > 0 and tokens[i - 1] not in {"в", "на", "к"}:
            continue
        hour, minute, j = int(tok), 0, i + 1
        if j < len(tokens) and tokens[j] in _HOUR_WORDS:
            j += 1
        if j < len(tokens) and tokens[j].isdigit() and int(tokens[j]) < 60:
            minute = int(tokens[j])
            j += 1
            if j < len(tokens) and tokens[j] in _MINUTE_WORDS:
                j += 1
        if j < len(tokens) and tokens[j] in _DAYPART:
            if _DAYPART[tokens[j]] == 12 and hour < 12:
                hour += 12
            elif tokens[j] == "ночи" and hour == 12:
                hour = 0
            j += 1
        if hour > 23:
            continue
        return Parsed((hour, minute), i, j, tokens)
    return None


_WEEKDAY_STEMS = ("понедельн", "вторн", "сред", "четверг", "пятниц", "суббот", "воскресен")
_MONTH_STEMS = (
    "январ", "феврал", "март", "апрел", "ма", "июн",
    "июл", "август", "сентябр", "октябр", "ноябр", "декабр",
)  # fmt: skip
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DOT_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")
_RELATIVE_DAYS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}


def _month(word: str) -> int | None:
    # "ма" would match too much: May is only "мая" / "май"
    if word in ("мая", "май"):
        return 5
    for number, stem in enumerate(_MONTH_STEMS, start=1):
        if number != 5 and word.startswith(stem):
            return number
    return None


def _future(day: int, month: int, today: date) -> date | None:
    """The next ``day.month`` on or after today (this year or the next)."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= today:
            return candidate
    return None


def parse_day(text: str, today: date) -> date | None:
    """A spoken day: "завтра", "в пятницу", "через 3 дня", "15 октября", "на выходных".

    Weekdays mean the next such day after today; dates without a year are the nearest
    future ones. Returns None if there is no recognizable day in the phrase.
    """
    if m := _ISO_DATE.search(text):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    if m := _DOT_DATE.search(text):
        return _future(int(m[1]), int(m[2]), today)
    words = normalize(text).split()
    for i in range(len(words)):
        if (day := _day_at(words, i, today)) is not None:
            return day
    return None


def _day_at(words: list[str], i: int, today: date) -> date | None:
    word = words[i]
    following = words[i + 1] if i + 1 < len(words) else ""
    if word in _RELATIVE_DAYS:
        return today + timedelta(days=_RELATIVE_DAYS[word])
    if word.isdigit() and (month := _month(following)):
        return _future(int(word), month, today)
    for weekday, stem in enumerate(_WEEKDAY_STEMS):
        if word.startswith(stem):
            return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)
    if word.startswith("выходн"):  # the coming Saturday
        return today + timedelta(days=(5 - today.weekday() - 1) % 7 + 1)
    if word == "через":
        return _after(words[i + 1 : i + 3], today)
    if word.startswith("следующ") and following.startswith("недел"):
        return today + timedelta(days=7 - today.weekday())  # next Monday
    return None


def _after(words: list[str], today: date) -> date | None:
    """ "[3] дня", "неделю", "2 месяца" after "через"."""
    count, unit = 1, words[0] if words else ""
    if unit.isdigit() and len(words) > 1:
        count, unit = int(unit), words[1]
    if unit.startswith(("день", "дня", "дней")):
        return today + timedelta(days=count)
    if unit.startswith("недел"):
        return today + timedelta(weeks=count)
    if unit.startswith("месяц"):
        return today + timedelta(days=30 * count)
    return None
