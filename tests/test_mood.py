from __future__ import annotations

from datetime import datetime
from typing import Any

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.core.events import EmotionChanged, MoodChanged, SpeechRecognized
from aion.core.mood import BOREDOM_FULL_S, Mood, day_energy
from tests.helpers import load_builtin_module

watch = load_builtin_module("companion", "watch")

HOUR = 3600


def at(hour: int, day: int = 7) -> float:
    return datetime(2026, 10, day, hour, 0).timestamp()


# -- mood model ---------------------------------------------------------------------------


def test_day_energy_curve() -> None:
    assert day_energy(4) < day_energy(9) < day_energy(16)
    assert day_energy(16) > day_energy(22)


def test_boredom_grows_in_silence_and_resets_on_talk() -> None:
    mood = Mood()
    mood.advance(at(12))
    mood.advance(at(12) + BOREDOM_FULL_S / 2)
    assert 0.45 < mood.boredom < 0.55
    mood.advance(at(12) + 10 * BOREDOM_FULL_S)
    assert mood.boredom == 1.0
    mood.on_user("привет")
    assert mood.boredom < 0.15


def test_energy_follows_time_of_day() -> None:
    mood = Mood()
    mood.advance(at(16))
    assert mood.energy > 0.7
    mood.advance(at(16) + 12 * HOUR)  # 4 a.m.
    assert mood.energy < 0.3


def test_kind_and_rude_words_change_affection() -> None:
    mood = Mood()
    mood.on_user("спасибо, ты умница")
    kind = mood.affection
    assert kind > 0.55
    mood.on_user("заткнись, дура")
    assert mood.affection < kind - 0.1
    mood.affection = 0.9
    mood.advance(at(12))
    mood.advance(at(12, day=13))  # two half-lives later: back toward neutral
    assert 0.55 < mood.affection < 0.65


def test_describe_is_gendered() -> None:
    sleepy = Mood(energy=0.2, affection=0.2, boredom=0.8)
    assert sleepy.describe(female=True) == (
        "сонная, говоришь тише и короче, обижена на собеседника, суховата, соскучилась по разговору"
    )
    assert "сонный" in sleepy.describe(female=False)
    assert Mood(energy=0.8).describe(female=True) == "бодрая и весёлая"


# -- service ------------------------------------------------------------------------------


async def test_mood_service_reacts_and_persists(app: Aion, app_store: ConfigStore) -> None:
    published: list[MoodChanged] = []
    app.bus.subscribe(MoodChanged, published.append)
    app.mood.mood.boredom = 0.9
    await app.bus.publish(SpeechRecognized(text="спасибо большое"))
    assert app.mood.mood.boredom < 0.15
    assert published
    await app.bus.publish(EmotionChanged(emotion="joy"))
    energy = app.mood.mood.energy
    await app.mood.stop()  # saves the mood

    again = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    await again.start()
    try:
        assert abs(again.mood.mood.energy - energy) < 0.05
        assert again.mood.note().startswith("[Твоё состояние сейчас:")
    finally:
        await again.stop()


# -- companion: when to speak up ----------------------------------------------------------


def situation(now: float, **kw: Any) -> Any:
    defaults: dict[str, Any] = {
        "hour": datetime.fromtimestamp(now).hour,
        "idle_s": 5.0,
        "fullscreen": False,
        "boredom": 0.7,
    }
    return watch.Situation(now=now, **(defaults | kw))


def test_small_talk_after_silence_only_once_unanswered() -> None:
    w = watch.Watch(watch.Settings(silence_minutes=40, break_minutes=0))
    start = at(14)
    assert w.check(situation(start)) == []
    assert w.check(situation(start + 30 * 60)) == []
    (obs,) = w.check(situation(start + 41 * 60, topics=["любит джаз"]))
    assert obs.text.startswith("Вы с пользователем молчите уже 41 минуту")
    assert obs.follow_up
    assert obs.importance > 0.6
    (again,) = w.check(situation(start + 82 * 60))
    assert again.key == "small_talk"
    assert w.check(situation(start + 200 * 60)) == []  # two unanswered: stop nagging
    w.user_talked(start + 200 * 60)
    assert w.check(situation(start + 230 * 60)) == []
    assert w.check(situation(start + 241 * 60))


def test_no_small_talk_in_quiet_hours_fullscreen_or_away() -> None:
    w = watch.Watch(watch.Settings(silence_minutes=10, break_minutes=0, night_reminder=False))
    start = at(23)
    w.check(situation(start))
    assert w.check(situation(start + HOUR)) == []  # midnight: quiet hours
    w = watch.Watch(watch.Settings(silence_minutes=10, break_minutes=0))
    start = at(14)
    w.check(situation(start))
    assert w.check(situation(start + HOUR, fullscreen=True)) == []
    assert w.check(situation(start + HOUR, idle_s=20 * 60)) == []


def test_greets_after_return() -> None:
    w = watch.Watch(watch.Settings(break_minutes=0))
    start = at(14)
    w.check(situation(start))
    assert w.check(situation(start + HOUR, idle_s=50 * 60)) == []
    (obs,) = w.check(situation(start + HOUR + 60, idle_s=2))
    assert obs.text == "Пользователь вернулся к компьютеру, его не было 51 минуту"
    # the time away is not silence: no small talk right after the greeting
    assert w.check(situation(start + HOUR + 120)) == []


def test_break_and_night_reminders() -> None:
    w = watch.Watch(watch.Settings(silence_minutes=600, break_minutes=120))
    start = at(14)
    w.check(situation(start))
    (obs,) = w.check(situation(start + 2 * HOUR + 60))
    assert "без перерыва уже 2 часа 1 минуту" in obs.text
    assert w.check(situation(start + 2 * HOUR + 120)) == []

    w = watch.Watch(watch.Settings(silence_minutes=600, break_minutes=0))
    night = at(2, day=8)
    (obs,) = w.check(situation(night))
    assert obs.text.startswith("Уже 2 часа ночи")
    assert w.check(situation(night + HOUR)) == []  # once a night
