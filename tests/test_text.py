from __future__ import annotations

import pytest

from aion.text import normalize, strip_wake_words, words_to_numbers


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("пять минут", "5 минут"),
        ("двадцать пять минут", "25 минут"),
        ("сто двадцать три", "123"),
        ("две тысячи двадцать шесть", "2026"),
        ("одну минуту", "1 минуту"),
        ("полторы минуты", "1.5 минуты"),
        ("ещё раз", "ещё раз"),
        ("без чисел", "без чисел"),
    ],
)
def test_words_to_numbers(src: str, expected: str) -> None:
    assert words_to_numbers(src) == expected


def test_normalize() -> None:
    assert normalize("Ещё раз: Какая ПОГОДА?!") == "еще раз какая погода"
    assert normalize("сколько будет 2.5 + 3,5?") == "сколько будет 2.5 + 3,5"


def test_strip_wake_words() -> None:
    names = ["аион", "айон"]
    assert strip_wake_words("Аион, который час?", names) == "который час?"
    assert strip_wake_words("айон который час", names) == "который час"
    assert strip_wake_words("аионовый час", names) == "аионовый час"


def test_parse_day() -> None:
    from datetime import date

    from aion.text.timeparse import parse_day

    wednesday = date(2026, 10, 7)
    cases = {
        "в пятницу": date(2026, 10, 9),
        "в среду": date(2026, 10, 14),  # the next one, not today
        "завтра вечером": date(2026, 10, 8),
        "послезавтра": date(2026, 10, 9),
        "через три дня": date(2026, 10, 10),
        "через 2 недели": date(2026, 10, 21),
        "15 октября": date(2026, 10, 15),
        "1 мая": date(2027, 5, 1),
        "на выходных": date(2026, 10, 10),
        "на следующей неделе": date(2026, 10, 12),
        "2026-11-03": date(2026, 11, 3),
        "09.10": date(2026, 10, 9),
    }
    for text, expected in cases.items():
        assert parse_day(text, wednesday) == expected, text
    assert parse_day("когда-нибудь", wednesday) is None
    assert parse_day("31 февраля", wednesday) is None


def test_whisper_hallucinations_are_dropped() -> None:
    from aion.stt.base import drop_hallucinations

    assert drop_hallucinations("Добавил субтитры DimaTorzok") == ""
    assert drop_hallucinations("Продолжение следует...") == ""
    assert drop_hallucinations("включи субтитры") == "включи субтитры"
    assert drop_hallucinations("Открой телеграм.") == "Открой телеграм."
