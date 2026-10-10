from __future__ import annotations

from typing import Any

import pytest

from aion.core import CommandSpec, Router


async def _noop(*_: Any, **__: Any) -> None:
    return None


def spec(name: str, *patterns: str, **kw: Any) -> CommandSpec:
    return CommandSpec(plugin="test", name=name, patterns=patterns, handler=_noop, **kw)


@pytest.fixture
def router() -> Router:
    r = Router(fuzzy_threshold=80)
    r.set_commands(
        [
            spec("time", "который час", "сколько времени", "(скажи|подскажи) время"),
            spec("weather", "[какая] погода", "погода в {city}", "какая погода в {city}"),
            spec(
                "timer", "(поставь|заведи) таймер на {minutes} минут", slot_types={"minutes": int}
            ),
            spec("volume", "громкость {level}", slot_types={"level": int}),
            spec("open", "(открой|запусти) {app}"),
        ]
    )
    return r


def test_exact(router: Router) -> None:
    m = router.match("Который час?")
    assert m is not None
    assert m.command.name == "time"
    assert m.score == 100


def _name(router: Router, text: str) -> str | None:
    m = router.match(text)
    return m.command.name if m else None


def test_alternatives_and_optional(router: Router) -> None:
    assert _name(router, "подскажи время") == "time"
    assert _name(router, "погода") == "weather"
    assert _name(router, "какая погода") == "weather"


def test_slots(router: Router) -> None:
    m = router.match("какая погода в Санкт-Петербурге")
    assert m is not None
    assert m.command.name == "weather"
    assert m.slots == {"city": "санкт-петербурге"}


def test_numeric_slot_from_words(router: Router) -> None:
    m = router.match("Поставь таймер на двадцать пять минут")
    assert m is not None
    assert m.slots == {"minutes": "25"}


def test_numeric_slot_rejects_text(router: Router) -> None:
    m = router.match("громкость побольше")
    assert m is None or m.command.name != "volume"


def test_leading_filler(router: Router) -> None:
    m = router.match("аион скажи пожалуйста который час")
    assert m is not None
    assert m.command.name == "time"
    assert 80 <= m.score < 100


def test_no_partial_word_match(router: Router) -> None:
    m = router.match("некоторый час")
    assert m is None or m.score < 100


def test_fuzzy_typo(router: Router) -> None:
    m = router.match("каторый час")
    assert m is not None
    assert m.command.name == "time"


def test_fuzzy_tail_slot(router: Router) -> None:
    m = router.match("откой блокнот")
    assert m is not None
    assert m.command.name == "open"
    assert m.slots == {"app": "блокнот"}


def test_unrelated_goes_to_fallback(router: Router) -> None:
    assert router.match("расскажи анекдот про программистов") is None
    assert router.match("") is None


def test_priority_breaks_ties() -> None:
    r = Router()
    r.set_commands([spec("a", "привет"), spec("b", "привет", priority=5)])
    m = r.match("привет")
    assert m is not None
    assert m.command.name == "b"


def test_optional_alternatives() -> None:
    r = Router()
    r.set_commands([spec("louder", "прибавь [громкость|звук]")])
    for phrase in ("прибавь", "прибавь звук", "прибавь громкость"):
        m = r.match(phrase)
        assert m is not None
        assert m.score == 100


def test_exact_match_beats_longer_fuzzy_superset() -> None:
    # every word of the phrase is in the longer pattern: token-set ratio alone says 100
    r = Router()
    r.set_commands(
        [
            spec("read", "прочитай [что] [у меня] (на экране|с экрана)"),
            spec("describe", "(что|посмотри) [у меня] на экране"),
        ]
    )
    m = r.match("что у меня на экране")
    assert m is not None
    assert m.command.name == "describe"
    assert m.score == 100
    fuzzy = r.match_all("что у меня на экране")[1]
    assert fuzzy.command.name == "read"
    assert fuzzy.score < 100
