"""Companion initiative: small talk after a long silence, greetings, care reminders."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

from aion.core.events import SpeechRecognized
from aion.llm.episodes import say_date
from aion.sdk import Plugin
from aion.sdk.desktop import fullscreen_app, user_idle_seconds

from .watch import Settings, Situation, Watch

CHECK_S = 20.0


class Companion(Plugin):
    async def on_load(self) -> None:
        self.watch = Watch(self.settings())
        self._unsubscribe = self.host.bus.subscribe(SpeechRecognized, self._on_user)
        self.create_task(self.loop(), name="companion")

    async def on_unload(self) -> None:
        self._unsubscribe()

    async def on_settings_changed(self) -> None:
        self.watch.settings = self.settings()

    def settings(self) -> Settings:
        c = self.config
        return Settings(
            silence_minutes=float(c["silence_minutes"]),
            break_minutes=float(c["break_minutes"]),
            quiet_from=int(c["quiet_from"]),
            quiet_to=int(c["quiet_to"]),
            greet_return=bool(c["greet_return"]),
            night_reminder=bool(c["night_reminder"]),
        )

    def _on_user(self, _: SpeechRecognized) -> None:
        self.watch.user_talked(time.time())

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_S)
            for obs in self.watch.check(await self.situation()):
                self.react(obs)

    async def situation(self) -> Situation:
        now = time.time()
        moment = datetime.fromtimestamp(now)
        brain = self.host.brain
        topics = await brain.facts.all() if brain is not None else []
        today = date.today()
        follow_ups = [
            f.topic + (f" ({say_date(date.fromisoformat(f.due), today)})" if f.due else "")
            for f in await self.host.diary.store.open_follow_ups(today)
        ]
        return Situation(
            now=now,
            hour=moment.hour + moment.minute / 60,
            idle_s=user_idle_seconds(),
            fullscreen=fullscreen_app(),
            boredom=self.host.mood.current().boredom,
            female=self.host.config.profile.gender == "female",
            topics=topics,
            follow_ups=follow_ups,
        )
