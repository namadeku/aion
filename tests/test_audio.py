"""Audio pipeline tests with a fake microphone, fake VAD and fake STT (no sound devices)."""

from __future__ import annotations

import asyncio
import queue
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aion.app import Aion
from aion.audio.pipeline import VoicePipeline
from aion.audio.segmenter import Segmenter, SpeechEnd, SpeechStart
from aion.audio.vad import FRAME, EnergyVad, Frame, Vad
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.core.events import WakeDetected
from aion.stt.base import Pcm, SttEngine
from aion.tts.textnorm import normalize_for_speech
from aion.wakeword.base import find_name, strip_name

# -- helpers ---------------------------------------------------------------------------

LOUD = 1000  # sample value used to mark "speech" frames for the fake VAD


def frames(seconds: float, *, speech: bool, tag: int = 0) -> list[Frame]:
    """Frames whose first sample carries a tag so the fake STT knows which phrase it got."""
    n = round(seconds * 16000 / FRAME)
    out: list[Frame] = []
    for _ in range(n):
        f = np.full(FRAME, LOUD if speech else 0, dtype=np.int16)
        f[0] = tag
        out.append(f)
    return out


class FakeVad(Vad):
    def __call__(self, frame: Frame) -> float:
        return 1.0 if frame[1] == LOUD else 0.0


class FakeMic:
    def __init__(self) -> None:
        self.frames: queue.Queue[Frame] = queue.Queue()

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def feed(self, chunks: list[Frame]) -> None:
        for f in chunks:
            self.frames.put(f)


class FakeStt(SttEngine):
    name = "fake"

    def __init__(self, phrases: dict[int, str]) -> None:
        super().__init__(models_dir=None)  # type: ignore[arg-type]
        self.phrases = phrases
        self.calls: list[float] = []

    async def load(self) -> None: ...

    async def transcribe(self, audio: Pcm) -> str:
        self.calls.append(len(audio) / 16000)
        tags = {int(t) for t in audio[::FRAME] if t}
        return " ".join(self.phrases[t] for t in sorted(tags))


@pytest.fixture
async def voice(
    app_store: ConfigStore,
) -> AsyncIterator[tuple[Aion, VoicePipeline, FakeMic, FakeStt]]:
    app_store.config.audio.vad.min_silence_ms = 200
    app_store.config.assistant.follow_up_seconds = 0
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    mic = FakeMic()
    stt = FakeStt(
        {
            1: "Айон, который час?",
            2: "который час",
            3: "Аион",
            4: "сколько времени",
            5: "просто разговор в комнате о погоде и всём таком",
        }
    )
    await aion.start()
    pipeline = VoicePipeline(aion, mic, FakeVad(), stt, wake=None)  # type: ignore[arg-type]
    await pipeline.start()
    yield aion, pipeline, mic, stt
    await pipeline.stop()
    await aion.stop()


async def wait_for(predicate: Any, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError
        await asyncio.sleep(0.02)


def spoken(aion: Aion) -> list[str]:
    assert isinstance(aion.speech, NullOutput)
    return aion.speech.spoken


# -- segmenter -------------------------------------------------------------------------


def test_segmenter_cuts_utterances() -> None:
    seg = Segmenter(min_silence_ms=200, min_speech_ms=100, pre_roll_ms=64)
    events = []
    for f in frames(0.3, speech=False) + frames(1.0, speech=True) + frames(0.5, speech=False):
        if (e := seg.push(f, 1.0 if f[1] == LOUD else 0.0)) is not None:
            events.append(e)
    assert isinstance(events[0], SpeechStart)
    assert isinstance(events[1], SpeechEnd)
    assert 0.9 < events[1].duration < 1.4


def test_segmenter_drops_clicks() -> None:
    seg = Segmenter(min_silence_ms=200, min_speech_ms=250)
    events = [
        e
        for f in frames(0.07, speech=True) + frames(0.5, speech=False)
        if (e := seg.push(f, 1.0 if f[1] == LOUD else 0.0)) is not None
    ]
    end = events[-1]
    assert isinstance(end, SpeechEnd)
    assert end.audio.size == 0


def test_segmenter_max_length() -> None:
    seg = Segmenter(max_utterance_s=1.0)
    ends = [e for f in frames(3.0, speech=True) if isinstance(e := seg.push(f, 1.0), SpeechEnd)]
    assert len(ends) >= 2


def test_energy_vad() -> None:
    vad = EnergyVad()
    assert vad(np.zeros(FRAME, dtype=np.int16)) == 0.0
    assert vad(np.full(FRAME, 8000, dtype=np.int16)) > 0.9


# -- wake word matching ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Айон, который час?", "аион"),
        ("аион который час", "аион"),
        ("эйон стоп", "эйон"),
        ("а ион который час", "аион"),
        ("который час", None),
        ("а я который час", None),
    ],
)
def test_find_name(text: str, expected: str | None) -> None:
    found = find_name(text, ["аион", "айон", "эйон", "aion"])
    assert (found[0] if found else None) == expected


def test_strip_name() -> None:
    names = ["аион", "айон"]
    assert strip_name("Айон, который час?", names) == "который час?"
    assert strip_name("а ион, который час", names) == "который час"
    assert strip_name("который час", names) == "который час"


# -- pipeline --------------------------------------------------------------------------


async def test_name_and_command_in_one_phrase(voice: Any) -> None:
    aion, _pipeline, mic, _stt = voice
    wakes: list[WakeDetected] = []
    aion.bus.subscribe(WakeDetected, wakes.append)
    mic.feed(
        frames(0.2, speech=False) + frames(1.5, speech=True, tag=1) + frames(0.6, speech=False)
    )
    await wait_for(lambda: spoken(aion))
    assert spoken(aion)[0].startswith("Сейчас ")
    assert [w.word for w in wakes] == ["аион"]


async def test_speech_without_name_is_ignored(voice: Any) -> None:
    aion, _pipeline, mic, stt = voice
    mic.feed(frames(2.0, speech=True, tag=5) + frames(0.6, speech=False))
    mic.feed(frames(0.5, speech=True, tag=2) + frames(0.6, speech=False))
    await asyncio.sleep(1.0)
    assert spoken(aion) == []
    # long chatter is checked by its prefix only; the short phrase is transcribed once
    assert len(stt.calls) == 2
    assert max(stt.calls) == pytest.approx(1.2, abs=0.1)


async def test_name_alone_then_command(voice: Any) -> None:
    aion, _pipeline, mic, _stt = voice
    mic.feed(frames(0.6, speech=True, tag=3) + frames(0.6, speech=False))
    await wait_for(lambda: aion.state.state == "listening")
    mic.feed(frames(0.8, speech=True, tag=4) + frames(0.6, speech=False))
    await wait_for(lambda: spoken(aion))
    assert spoken(aion)[0].startswith("Сейчас ")


async def test_push_to_talk(voice: Any) -> None:
    aion, pipeline, mic, _stt = voice
    await pipeline.push_to_talk()
    assert aion.state.state == "listening"
    mic.feed(frames(0.8, speech=True, tag=2) + frames(0.6, speech=False))
    await wait_for(lambda: spoken(aion))
    assert spoken(aion)[0].startswith("Сейчас ")


async def test_answer_to_question_needs_no_name(voice: Any) -> None:
    aion, _pipeline, mic, _stt = voice
    answer: list[str | None] = []

    async def ask() -> None:
        answer.append(await aion.dialog.ask("Какой город?", timeout=5))

    task = asyncio.create_task(ask())
    await wait_for(lambda: aion.dialog.awaiting_answer)
    mic.feed(frames(0.8, speech=True, tag=2) + frames(0.6, speech=False))
    await task
    assert answer == ["который час"]


async def test_mute(voice: Any) -> None:
    aion, pipeline, mic, stt = voice
    pipeline.set_muted(True)
    mic.feed(frames(1.5, speech=True, tag=1) + frames(0.6, speech=False))
    await asyncio.sleep(0.5)
    assert stt.calls == []
    assert spoken(aion) == []


# -- TTS text normalization ------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("Сейчас 19:05.", "Сейчас девятнадцать ноль пять."),
        ("Сейчас 7:00.", "Сейчас семь часов."),
        ("+14°, ощущается как -3°", "плюс четырнадцать градусов, ощущается как минус три градуса"),
        ("Ветер 9 м/с.", "Ветер девять метров в секунду."),
        ("Таймер на 1 минуту", "Таймер на одну минуту"),
        ("2 минуты и 21 секунда", "две минуты и двадцать одна секунда"),
        ("Будет 12,5.", "Будет двенадцать целых пять десятых."),
        ("Осадки 80%", "Осадки восемьдесят процентов"),
        ("5 км — это 3,107 мили", "пять километров, это три целых сто семь тысячных мили"),
    ],
)
def test_normalize_for_speech(src: str, expected: str) -> None:
    assert normalize_for_speech(src) == expected


async def test_online_voice_falls_back_to_offline(app_store: ConfigStore) -> None:
    from aion.audio.player import AudioPlayer
    from aion.core import EventBus, StateMachine
    from aion.tts.base import Audio, TtsEngine, VoiceInfo
    from aion.tts.output import VoiceOutput

    class Broken(TtsEngine):
        name = "edge"
        offline = False

        async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
            raise ConnectionError("no internet")

        async def voices(self, language: str = "ru") -> list[VoiceInfo]:
            return []

    used: list[str] = []

    class Offline(TtsEngine):
        name = "piper"

        async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
            used.append(voice)
            return Audio(np.zeros(100, dtype=np.float32), 22050)

        async def voices(self, language: str = "ru") -> list[VoiceInfo]:
            return []

    app_store.update(
        {"profiles": {"aion": {"gender": "female", "voice": {"engine": "edge", "voice": "x"}}}},
        save=False,
    )
    bus = EventBus()
    out = VoiceOutput(bus, StateMachine(bus), app_store, AudioPlayer())
    out._engines = {"edge": Broken(Path(".")), "piper": Offline(Path("."))}  # pyright: ignore[reportPrivateUsage]
    audio = await out._synthesize("Привет")  # pyright: ignore[reportPrivateUsage]
    assert audio.samples.size == 100
    assert used == ["ru_RU-irina-medium"]
