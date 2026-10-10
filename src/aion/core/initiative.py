"""Initiative: unprompted remarks on what is happening (game events, system events...).

Plugins only *report* observations (``Plugin.react``); this service decides whether and when
to speak, so that remarks do not interrupt the user, do not pile up and match the configured
talkativeness:

* an observation below the talkativeness threshold is dropped, as is a repeat of the same
  ``key`` within its cooldown;
* a burst of observations (three kills in two seconds) is gathered into one remark;
* the remark waits until nobody is talking and a minimal gap since the previous remark has
  passed (urgent ones skip the gap); one that waited longer than its ``ttl`` is stale and
  dropped;
* the LLM phrases the remark in character, with the dialog history; if it is off or slow,
  a ready-made ``quick`` phrase is said instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from aion.llm.base import Message

if TYPE_CHECKING:
    from aion.app import Aion
    from aion.config.schema import InitiativeConfig
    from aion.core.dialog import Turn

URGENT = 0.9  # importance from which a remark skips the gap since the previous one
GATHER_S = 0.8  # how long to collect a burst of observations before speaking
POLL_S = 0.2  # how often to check whether the dialog became free


@dataclass
class Observation:
    """Something worth a remark. ``text`` tells the LLM what happened (not what to say)."""

    text: str
    importance: float = 0.5  # 0..1: how remarkable it is
    key: str = ""  # observations with the same key share a cooldown
    cooldown: float = 0.0  # seconds before the same key may be reported again
    ttl: float = 10.0  # seconds after which the remark is too late
    quick: list[str] = field(default_factory=list)  # ready-made phrases, one is picked
    emotion: str | None = None  # avatar emotion for a ready-made phrase
    follow_up: bool = False  # listen without the wake word after the remark
    source: str = ""  # plugin name
    created: float = field(default_factory=time.monotonic)

    def fresh(self, now: float) -> bool:
        return now - self.created <= self.ttl


def min_importance(talkativeness: float) -> float:
    """0.5 talkativeness lets through observations of importance 0.5 and above."""
    return 1.0 - talkativeness


def min_gap(talkativeness: float) -> float:
    """Seconds between two remarks: 24 s for a quiet assistant, 4 s for a chatterbox."""
    return 4.0 + 20.0 * (1.0 - talkativeness)


class Initiative:
    def __init__(self, app: Aion) -> None:
        self.app = app
        self.gather_s = GATHER_S
        self._pending: list[Observation] = []
        self._cooldowns: dict[str, float] = {}
        self._last_remark = float("-inf")
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def config(self) -> InitiativeConfig:
        return self.app.config.initiative

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="initiative")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._pending.clear()

    def submit(self, obs: Observation) -> bool:
        """Queue an observation. Returns False if it was dropped right away."""
        config = self.config
        now = time.monotonic()
        if not config.enabled or obs.importance < min_importance(config.talkativeness):
            logger.debug("Событие без реплики: {}", obs.text)
            return False
        key = f"{obs.source}:{obs.key or obs.text}"
        if now < self._cooldowns.get(key, float("-inf")):
            return False
        if obs.cooldown:
            self._cooldowns[key] = now + obs.cooldown
        self._pending.append(obs)
        self._wake.set()
        return True

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await asyncio.sleep(self.gather_s)
            while batch := await self._next_batch():
                try:
                    await self._remark(batch)
                except Exception:
                    logger.exception("Не удалось прокомментировать событие")
                self._last_remark = time.monotonic()

    async def _next_batch(self) -> list[Observation]:
        """Wait for a moment to speak; return the observations still worth a remark."""
        while True:
            now = time.monotonic()
            self._pending = [o for o in self._pending if o.fresh(now)]
            if not self._pending:
                return []
            urgent = max(o.importance for o in self._pending) >= URGENT
            gap_over = now - self._last_remark >= min_gap(self.config.talkativeness)
            if self.app.dialog.free and (urgent or gap_over):
                batch, self._pending = self._pending, []
                return batch
            await asyncio.sleep(POLL_S)

    async def _remark(self, batch: list[Observation]) -> None:
        batch.sort(key=lambda o: o.created)
        top = max(batch, key=lambda o: o.importance)
        follow_up = any(o.follow_up for o in batch)
        summary = "; ".join(o.text for o in batch)
        logger.info("Реплика по событию: {}", summary)
        dialog = self.app.dialog
        brain = self.app.brain
        if brain is not None and self.config.use_llm:
            prompt = self._prompt(batch)
            timeout = self.config.llm_timeout

            async def by_llm(turn: Turn) -> bool:
                return await brain.comment(turn, prompt, f"[событие] {summary}", timeout)

            said = await dialog.proactive(by_llm, summary, follow_up=follow_up)
            if said is not False:  # said it, or the user interrupted
                return
        phrases = top.quick or [p for o in batch for p in o.quick]
        if not phrases:
            return
        phrase = random.choice(phrases)

        async def quick(turn: Turn) -> bool:
            await turn.say(phrase, emotion=top.emotion)
            return True

        if await dialog.proactive(quick, summary, follow_up=follow_up) and brain is not None:
            brain.memory.add_turn(
                [Message("user", f"[событие] {summary}"), Message("assistant", phrase)]
            )

    def _prompt(self, batch: list[Observation]) -> str:
        config = self.app.config
        address = config.assistant.user_address
        female = config.profile.gender == "female"
        noticed = "заметила сама" if female else "заметил сам"
        events = "\n".join(f"- {o.text}" for o in batch)
        return (
            f"[Это не слова собеседника, а то, что ты сейчас {noticed}:]\n{events}\n"
            f"[Отреагируй от себя одной короткой живой репликой (до 15 слов), обращаясь к "
            f"«{address}». Не пересказывай событие дословно и не вызывай инструменты.]"
        )
