"""Fuzzy wake-name spotting: spelling variants, filler words and false-trigger guards."""

from __future__ import annotations

import pytest

from aion.wakeword.base import find_name, sensitivity_to_threshold, strip_name
from aion.wakeword.names import fold, match_name

NAMES = ["аион", "айон", "эйон", "aion"]
THRESHOLD = sensitivity_to_threshold(0.5)


@pytest.mark.parametrize(
    "text",
    [
        "Аион, который час",
        "Айон который час",
        "а ион который час",
        "ай он который час",
        "Эйон, поставь таймер",
        "Aion stop",
        "аэон включи звук",
        "Слушай, Аион, сделай скриншот",
        "Эй, Аион, привет",
        "ну Аион выключи свет",
        "аилон включи свет",  # one inserted consonant
        "аион",
    ],
)
def test_name_is_found(text: str) -> None:
    found = match_name(text, NAMES, THRESHOLD)
    assert found is not None, text
    assert found.where == "head"
    assert found.score >= THRESHOLD


@pytest.mark.parametrize(
    "text",
    [
        "Ион натрия имеет положительный заряд",
        "Лион очень красивый город",
        "Они сказали что придут завтра",
        "Эй, он опять забыл выключить свет",  # the comma keeps "эй" and "он" apart
        "я и он пошли домой",
        "Аня, который час",
        "Неоновая вывеска горела всю ночь",
        "Алло, Иван, ты уже дома",
        "Железный человек, айрон мэн",
        "Эон это огромный период времени",
        "Меня зовут Аион и я ваш ассистент",  # the name is not the first word
        "Конечно сэр, Аион к вашим услугам",
        "",
        "который час",
    ],
)
def test_other_speech_is_not_a_call(text: str) -> None:
    assert match_name(text, NAMES, THRESHOLD) is None, text


def test_spellings_fold_to_the_same_string() -> None:
    assert fold("аион") == fold("айон") == fold("Aion") == fold("аэон") == fold("а ион")


def test_exact_alias_beats_confusable_blacklist() -> None:
    # "ион" is a blacklisted word, but a user may well name the assistant that
    assert match_name("ион включи свет", ["ион"], THRESHOLD) is not None
    assert match_name("ион включи свет", NAMES, THRESHOLD) is None


def test_trailing_name_needs_opt_in_and_high_similarity() -> None:
    assert match_name("который час Аион", NAMES, THRESHOLD) is None
    tail = match_name("который час, Аион", NAMES, THRESHOLD, tail_threshold=90)
    assert tail is not None
    assert (tail.where, tail.start, tail.end) == ("tail", 2, 3)
    assert match_name("который час лион", NAMES, THRESHOLD, tail_threshold=90) is None


def test_sensitivity_changes_strictness() -> None:
    near = "аидон включи"  # two edits away after folding
    assert match_name(near, NAMES, sensitivity_to_threshold(0.0)) is None
    assert match_name(near, NAMES, sensitivity_to_threshold(1.0)) is not None


@pytest.mark.parametrize(
    ("text", "rest"),
    [
        ("Аион, который час", "который час"),
        ("а ион который час", "который час"),
        ("Слушай, Айон, включи звук", "включи звук"),
        ("Аион", ""),
        ("который час", "который час"),
    ],
)
def test_strip_name(text: str, rest: str) -> None:
    assert strip_name(text, NAMES, THRESHOLD) == rest


def test_find_name_compat_wrapper() -> None:
    found = find_name("а ион, который час", NAMES, THRESHOLD)
    assert found == ("аион", 2)
    assert find_name("Меня зовут Аион", NAMES, THRESHOLD) is None
