"""When a companion speaks up by itself: long silence, a return, a long session, late night.

Pure decision logic: :meth:`Watch.check` gets the current situation and returns observations;
the plugin feeds it real sensors every few seconds.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from aion.core.initiative import Observation
from aion.text.plural import fmt_count

AWAY_S = 10 * 60  # no input this long: the user is away from the computer
BACK_S = 60  # input within this: the user is at the computer
RETURN_AFTER_S = 30 * 60  # greet the user back after an absence this long
MAX_UNANSWERED = 2  # stop starting conversations nobody answers

MINUTES = ("минуту", "минуты", "минут")
HOURS = ("час", "часа", "часов")


def fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return fmt_count(minutes, MINUTES)
    hours, rest = divmod(minutes, 60)
    return fmt_count(hours, HOURS) + (f" {fmt_count(rest, MINUTES)}" if rest else "")


@dataclass
class Settings:
    silence_minutes: float = 40
    break_minutes: float = 120  # 0: no break reminders
    quiet_from: int = 23  # hour; no small talk from quiet_from to quiet_to
    quiet_to: int = 9
    greet_return: bool = True
    night_reminder: bool = True


@dataclass
class Situation:
    now: float  # unix time
    hour: float  # local hour of day, 0..24
    idle_s: float | None  # seconds since the last input, None if unknown
    fullscreen: bool  # a game or a video is on the screen
    boredom: float  # 0..1, from the mood
    female: bool = True
    topics: list[str] = field(default_factory=list)  # facts the character may ask about
    follow_ups: list[str] = field(default_factory=list)  # due "how did it go?" questions


def in_hours(hour: float, start: int, end: int) -> bool:
    """``start <= hour < end``, across midnight when ``start > end``."""
    if start == end:
        return False
    return start <= hour < end if start < end else hour >= start or hour < end


class Watch:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.last_talk = 0.0  # unix time the user last said something (or start)
        self.last_chat = 0.0  # when the character last started a conversation
        self.unanswered = 0
        self.away_since: float | None = None
        self.active_since: float | None = None
        self.night_reminded: float = 0.0
        self.asked: set[str] = set()  # follow-ups already raised by small talk

    def user_talked(self, now: float) -> None:
        self.last_talk = now
        self.unanswered = 0

    def check(self, s: Situation) -> list[Observation]:
        if not self.last_talk:
            self.last_talk = s.now
        out: list[Observation] = []
        out += self._presence(s)
        if s.fullscreen or self.away_since is not None:
            return out  # playing, watching or away: no small talk
        out += self._long_session(s)
        out += self._night(s)
        if not out:
            out += self._silence(s)
        return out

    # -- presence -------------------------------------------------------------------------

    def _presence(self, s: Situation) -> list[Observation]:
        if s.idle_s is None:  # unknown: assume the user is here
            if self.active_since is None:
                self.active_since = s.now
            return []
        if s.idle_s >= AWAY_S:
            if self.away_since is None:
                self.away_since = s.now - s.idle_s
            self.active_since = None
            return []
        if self.active_since is None:
            self.active_since = s.now - s.idle_s
        if self.away_since is None or s.idle_s > BACK_S:
            return []
        away = s.now - self.away_since
        self.away_since = None
        self.last_talk = max(self.last_talk, s.now)  # the silence while away doesn't count
        if not self.settings.greet_return or away < RETURN_AFTER_S:
            return []
        return [
            Observation(
                f"Пользователь вернулся к компьютеру, его не было {fmt_duration(away)}",
                importance=0.55,
                key="return",
                follow_up=True,
                quick=[
                    "С возвращением!",
                    "О, с возвращением! Я ждала." if s.female else "О, с возвращением! Я ждал.",
                ],
                emotion="joy",
            )
        ]

    # -- care -------------------------------------------------------------------------------

    def _long_session(self, s: Situation) -> list[Observation]:
        minutes = self.settings.break_minutes
        if not minutes or self.active_since is None:
            return []
        active = s.now - self.active_since
        if active < minutes * 60:
            return []
        self.active_since = s.now  # next reminder after another full period
        return [
            Observation(
                f"Пользователь сидит за компьютером без перерыва уже {fmt_duration(active)}; "
                "мягко предложи размяться или отдохнуть",
                importance=0.6,
                key="break",
                quick=["Давно без перерыва. Может, размяться и выпить чаю?"],
                emotion="thinking",
            )
        ]

    def _night(self, s: Situation) -> list[Observation]:
        if not self.settings.night_reminder or not 1 <= s.hour < 5:
            return []
        if s.now - self.night_reminded < 12 * 3600:
            return []
        self.night_reminded = s.now
        return [
            Observation(
                f"Уже {fmt_count(int(s.hour), HOURS)} ночи, а пользователь всё ещё за компьютером; "
                "заботливо напомни про сон",
                importance=0.6,
                key="night",
                quick=["Уже глубокая ночь. Может, пора спать?"],
                emotion="sad",
            )
        ]

    # -- small talk -------------------------------------------------------------------------

    def _silence(self, s: Situation) -> list[Observation]:
        settings = self.settings
        if in_hours(s.hour, settings.quiet_from, settings.quiet_to):
            return []
        if self.unanswered >= MAX_UNANSWERED:
            return []
        silent = s.now - max(self.last_talk, self.last_chat)
        if silent < settings.silence_minutes * 60:
            return []
        self.last_chat = s.now
        self.unanswered += 1
        first = "первой" if s.female else "первым"
        bored = "тебе скучно" if s.boredom > 0.6 else "хочется поговорить"
        return [
            Observation(
                f"Вы с пользователем молчите уже {fmt_duration(silent)}, {bored}. "
                f"Заговори {first}: {self._topic(s)}",
                importance=0.35 + 0.5 * s.boredom,
                key="small_talk",
                follow_up=True,
                ttl=60,
                quick=["Как дела?", "Поболтаем немного?", "Тишина какая-то... Как настроение?"],
            )
        ]

    def _topic(self, s: Situation) -> str:
        # a due question from the diary is the best reason to speak up
        for topic in s.follow_ups:
            if topic not in self.asked:
                self.asked.add(topic)
                return f"спроси, как дела с этим: {topic}"
        options = [
            "спроси, чем пользователь сейчас занят",
            "поделись коротким забавным фактом или своей мыслью и спроси его мнение",
        ]
        if s.topics:
            fact = random.choice(s.topics)
            options += [f"вспомни, что ты знаешь о нём («{fact}»), и спроси об этом"] * 2
        if 6 <= s.hour < 11:
            options.append("пожелай доброго утра и спроси о планах на день")
        elif 18 <= s.hour < 23:
            options.append("спроси, как прошёл его день")
        return random.choice(options)
