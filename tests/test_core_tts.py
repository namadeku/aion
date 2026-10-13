"""VoiceOutput: with no internet an online voice is tried once, then the offline one speaks."""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest

from aion.config import ConfigStore
from aion.core import EventBus, StateMachine
from aion.tts import output as tts_output
from aion.tts.base import Audio, TtsEngine, VoiceInfo
from aion.tts.output import VoiceOutput


class FakePlayer:
    def start(self) -> None: ...

    def close(self) -> None: ...

    def stop(self) -> None: ...


class FakeEngine(TtsEngine):
    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(None)  # pyright: ignore[reportArgumentType]
        self.fail = fail
        self.calls = 0

    async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("no route")
        return Audio(np.zeros(100, dtype=np.float32), 22050)

    async def voices(self, language: str = "ru") -> list[VoiceInfo]:
        return []


class OnlineEngine(FakeEngine):
    offline = False


def make_output(store: ConfigStore, online: OnlineEngine, offline: FakeEngine) -> VoiceOutput:
    bus = EventBus()
    out = VoiceOutput(bus, StateMachine(bus), store, FakePlayer())  # pyright: ignore[reportArgumentType]
    store.config.profile.voice.engine = "edge"
    store.config.profile.voice.voice = "ru-RU-SvetlanaNeural"
    out._engines = {"edge": online, "piper": offline}  # pyright: ignore[reportPrivateUsage]
    return out


async def test_failed_online_voice_is_not_retried_for_every_phrase(store: ConfigStore) -> None:
    online, offline = OnlineEngine(fail=True), FakeEngine()
    out = make_output(store, online, offline)
    for _ in range(4):
        audio = await out._synthesize("Привет")  # pyright: ignore[reportPrivateUsage]
        assert len(audio.samples) == 100
    assert online.calls == 1  # one failure, then straight to the offline voice
    assert offline.calls == 4


async def test_online_voice_is_tried_again_after_the_pause(
    store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    online, offline = OnlineEngine(fail=True), FakeEngine()
    out = make_output(store, online, offline)
    await out._synthesize("раз")  # pyright: ignore[reportPrivateUsage]
    clock: dict[str, Any] = {"now": tts_output.time.monotonic() + tts_output.ONLINE_RETRY_S + 1}
    monkeypatch.setattr(tts_output.time, "monotonic", lambda: clock["now"])
    online.fail = False  # the internet is back
    await out._synthesize("два")  # pyright: ignore[reportPrivateUsage]
    assert online.calls == 2
    assert offline.calls == 1
