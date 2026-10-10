"""The character's mood: a few slow numbers that colour its tone, animation and initiative.

* ``energy`` (0..1) follows the time of day (sleepy at night, lively in the afternoon) and is
  lifted by conversation and joy;
* ``affection`` (0..1) grows with kind words and drops after rude ones, then slowly returns
  to neutral over days;
* ``boredom`` (0..1) builds up in silence (full after two hours) and vanishes when the user
  talks — the companion plugin uses it to start a conversation.

The model is pure (time is passed in) and persisted in the key-value store, so the mood
survives a restart. The LLM sees it as a short note on the latest message, not in the system
prompt, which has to stay byte-identical for the prompt cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from aion.core.events import EmotionChanged, MoodChanged, SpeechRecognized
from aion.text import normalize

if TYPE_CHECKING:
    from aion.app import Aion

BOREDOM_FULL_S = 2 * 3600  # silence until boredom is full
ENERGY_TAU_S = 40 * 60  # how fast energy drifts to the time-of-day level
AFFECTION_HALF_LIFE_S = 3 * 24 * 3600  # how fast affection returns to neutral
TICK_S = 60.0
PUBLISH_DELTA = 0.05  # publish a mood change to the UI only when it is noticeable

PRAISE = {
    "спасибо", "благодарю", "молодец", "умница", "умничка", "супер", "отлично", "класс",
    "люблю", "обожаю", "лучшая", "лучший", "красавица", "красавчик", "милая", "милый",
    "thanks", "thank",
}  # fmt: skip
RUDE = {
    "дура", "дурак", "тупая", "тупой", "идиотка", "идиот", "заткнись", "отстань", "бесишь",
    "бесполезная", "бесполезный", "глупая", "глупый", "shut", "stupid",
}  # fmt: skip
EMOTION_EFFECT = {  # emotion -> (energy, affection) nudge
    "joy": (0.04, 0.01),
    "surprise": (0.03, 0.0),
    "sad": (-0.03, 0.0),
    "angry": (0.02, -0.02),
}


def day_energy(hour: float) -> float:
    """Energy the character tends to at this hour: low at night, highest in the afternoon."""
    # a smooth curve: minimum at 4:00 (0.2), maximum at 16:00 (0.8)
    return 0.5 - 0.3 * math.cos((hour - 4) / 24 * 2 * math.pi)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass
class Mood:
    energy: float = 0.6
    affection: float = 0.5
    boredom: float = 0.0
    updated: float = 0.0  # unix time of the last update

    def advance(self, now: float) -> None:
        """Let time pass: boredom grows, energy drifts to the time of day, affection settles."""
        if not self.updated:
            self.updated = now
            self.energy = day_energy(_hour(now))
            return
        dt = max(0.0, now - self.updated)
        self.updated = now
        if not dt:
            return
        self.boredom = _clamp(self.boredom + dt / BOREDOM_FULL_S)
        target = day_energy(_hour(now))
        self.energy = target + (self.energy - target) * math.exp(-dt / ENERGY_TAU_S)
        self.affection = 0.5 + (self.affection - 0.5) * 0.5 ** (dt / AFFECTION_HALF_LIFE_S)

    def on_user(self, text: str) -> None:
        """The user talked to the character."""
        words = set(normalize(text, numbers=False).split())
        self.boredom *= 0.1
        self.energy = _clamp(self.energy + 0.03)
        self.affection = _clamp(self.affection + 0.005)
        if words & PRAISE:
            self.affection = _clamp(self.affection + 0.06)
            self.energy = _clamp(self.energy + 0.03)
        if words & RUDE:
            self.affection = _clamp(self.affection - 0.12)

    def on_emotion(self, emotion: str) -> None:
        energy, affection = EMOTION_EFFECT.get(emotion, (0.0, 0.0))
        self.energy = _clamp(self.energy + energy)
        self.affection = _clamp(self.affection + affection)

    def describe(self, female: bool) -> str:
        """A few words for the LLM: ``бодрая, соскучилась по разговору``."""

        def g(masculine: str, feminine: str) -> str:
            return feminine if female else masculine

        parts: list[str] = []
        if self.energy < 0.3:
            parts.append(g("сонный, говоришь тише и короче", "сонная, говоришь тише и короче"))
        elif self.energy < 0.45:
            parts.append(g("немного устал", "немного устала"))
        elif self.energy > 0.7:
            parts.append(g("бодрый и весёлый", "бодрая и весёлая"))
        else:
            parts.append(g("спокоен", "спокойна"))
        if self.affection < 0.3:
            parts.append(g("обижен на собеседника, суховат", "обижена на собеседника, суховата"))
        elif self.affection > 0.75:
            parts.append("очень тепло относишься к собеседнику")
        if self.boredom > 0.6:
            parts.append(g("соскучился по разговору", "соскучилась по разговору"))
        return ", ".join(parts)

    def note(self, female: bool) -> str:
        return (
            f"[Твоё состояние сейчас: {self.describe(female)}. Пусть оно слегка окрашивает "
            "тон ответа; не говори о нём прямо, если не спросят.]"
        )


def _hour(now: float) -> float:
    moment = datetime.fromtimestamp(now)
    return moment.hour + moment.minute / 60


class MoodService:
    """Keeps the mood up to date from dialog events and time; persists and publishes it."""

    def __init__(self, app: Aion) -> None:
        self.app = app
        self.mood = Mood()
        self._published = Mood()
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe = [
            app.bus.subscribe(SpeechRecognized, self._on_user),
            app.bus.subscribe(EmotionChanged, self._on_emotion),
        ]

    async def start(self) -> None:
        saved = await self.app.db.kv("core").get("mood")
        if isinstance(saved, dict):
            with contextlib.suppress(TypeError):
                self.mood = Mood(**saved)  # pyright: ignore[reportUnknownArgumentType]
        self.mood.advance(time.time())
        await self._publish(force=True)
        self._task = asyncio.get_running_loop().create_task(self._run(), name="mood")

    async def stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._save()

    def current(self) -> Mood:
        self.mood.advance(time.time())
        return self.mood

    def note(self) -> str:
        return self.current().note(self.app.config.profile.gender == "female")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            self.mood.advance(time.time())
            await self._publish()
            await self._save()

    async def _on_user(self, event: SpeechRecognized) -> None:
        self.mood.advance(time.time())
        self.mood.on_user(event.text)
        await self._publish()

    async def _on_emotion(self, event: EmotionChanged) -> None:
        if event.emotion in EMOTION_EFFECT:
            self.mood.advance(time.time())
            self.mood.on_emotion(event.emotion)
            await self._publish()

    async def _publish(self, *, force: bool = False) -> None:
        mood, last = self.mood, self._published
        changed = max(
            abs(mood.energy - last.energy),
            abs(mood.affection - last.affection),
            abs(mood.boredom - last.boredom),
        )
        if not force and changed < PUBLISH_DELTA:
            return
        self._published = Mood(**asdict(mood))
        await self.app.bus.publish(MoodChanged(**self.state()))

    def state(self) -> dict[str, Any]:
        mood = self.mood
        return {
            "energy": round(mood.energy, 3),
            "affection": round(mood.affection, 3),
            "boredom": round(mood.boredom, 3),
        }

    async def _save(self) -> None:
        try:
            await self.app.db.kv("core").set("mood", asdict(self.mood))
        except Exception:
            logger.exception("Не удалось сохранить настроение")
