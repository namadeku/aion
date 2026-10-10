"""Spoken output: TTS engine + effects + audio player behind the ``SpeechOutput`` queue."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from pathlib import Path

from loguru import logger

from aion.audio import effects
from aion.audio.player import AudioPlayer
from aion.config import ConfigStore, VoiceConfig
from aion.core.bus import EventBus
from aion.core.speech import SpeechOutput
from aion.core.state import StateMachine
from aion.models import Progress
from aion.tts.base import Audio, TtsEngine
from aion.tts.textnorm import normalize_for_speech

OFFLINE_VOICES = {"female": "ru_RU-irina-medium", "male": "ru_RU-denis-medium"}


def create_engine(
    name: str, models_dir: Path, progress: Progress | None = None, voice: VoiceConfig | None = None
) -> TtsEngine:
    match name:
        case "piper":
            from aion.tts.piper_engine import PiperEngine

            return PiperEngine(models_dir, progress)
        case "edge":
            from aion.tts.edge_engine import EdgeEngine

            return EdgeEngine(models_dir, progress)
        case "silero":
            from aion.tts.silero_engine import SileroEngine

            return SileroEngine(models_dir, progress)
        case "xtts":
            from aion.tts.xtts_engine import XttsEngine

            return XttsEngine(
                models_dir, progress, reference_wav=voice.reference_wav if voice else None
            )
        case _:
            raise ValueError(f"Неизвестный TTS-движок {name!r}")


async def render_voice(engine: TtsEngine, text: str, voice: VoiceConfig) -> Audio:
    """Synthesize ``text`` with all voice settings applied (rate, pitch, effect, volume)."""
    spoken = text if engine.reads_numbers else normalize_for_speech(text)
    # Pitch shift without changing tempo: synthesize slower, then play back faster.
    factor = 2 ** (voice.pitch / 12)
    audio = await engine.synthesize(spoken, voice=voice.voice, rate=voice.rate / factor)
    rate = round(audio.sample_rate * factor)
    samples = await asyncio.to_thread(
        effects.apply, audio.samples, rate, voice.effect, voice.volume
    )
    return Audio(samples, rate)


class VoiceOutput(SpeechOutput):
    def __init__(
        self,
        bus: EventBus,
        state: StateMachine,
        store: ConfigStore,
        player: AudioPlayer,
        progress: Progress | None = None,
    ) -> None:
        super().__init__(bus, state)
        self.store = store
        self.player = player
        self.progress = progress
        self._engines: dict[str, TtsEngine] = {}
        self._ahead: dict[str, deque[asyncio.Task[Audio]]] = defaultdict(deque)
        self._synth_lock = asyncio.Lock()
        # engines that failed to load (e.g. Silero without torch): speak with Piper instead
        self._unavailable: set[str] = set()
        #: The voice failed to start: replies are shown as text only (no download retries).
        self.silent = False

    @property
    def voice(self) -> VoiceConfig:
        return self.store.config.profile.voice

    def engine(self, name: str | None = None) -> TtsEngine:
        name = name or self.voice.engine
        if name not in self._engines:
            self._engines[name] = create_engine(
                name, self.store.config.paths.models, self.progress, self.voice
            )
        return self._engines[name]

    async def start(self) -> None:
        self.player.start()
        await super().start()

    async def close(self) -> None:
        await super().close()
        self.player.close()

    async def prepare(self, text: str) -> None:
        if self.silent:
            return
        self._ahead[text].append(asyncio.create_task(self._synthesize(text)))

    async def render(self, text: str) -> None:
        if self.silent:
            return
        queue = self._ahead.get(text)
        task = queue.popleft() if queue else asyncio.create_task(self._synthesize(text))
        if queue is not None and not queue:
            self._ahead.pop(text, None)
        try:
            audio = await task
        except asyncio.CancelledError:
            task.cancel()
            raise
        await self.player.play(audio.samples, audio.sample_rate)

    async def stop(self) -> None:
        for tasks in self._ahead.values():
            for task in tasks:
                task.cancel()
        self._ahead.clear()
        self.player.stop()
        await super().stop()

    async def _synthesize(self, text: str) -> Audio:
        async with self._synth_lock:
            if self.voice.engine in self._unavailable:
                return await self._render_offline(text)
            engine = self.engine()
            try:
                return await render_voice(engine, text, self.voice)
            except Exception as e:
                if engine.offline:
                    logger.exception("Ошибка синтеза речи ({})", self.voice.engine)
                    raise
                # online voice unavailable (no internet?): keep talking with an offline one
                logger.warning("{} недоступен ({}), говорю офлайн-голосом", self.voice.engine, e)
                return await self._render_offline(text)

    async def _render_offline(self, text: str) -> Audio:
        fallback = self.voice.model_copy(update={"engine": "piper", "voice": self.offline_voice()})
        return await render_voice(self.engine("piper"), text, fallback)

    def offline_voice(self) -> str:
        female = self.store.config.profile.gender == "female"
        return OFFLINE_VOICES["female" if female else "male"]

    async def warm_up(self) -> None:
        """Download and load the current voice (and the offline fallback for online voices)."""
        engine = self.engine()
        if not engine.offline:
            await self.engine("piper").prepare(self.offline_voice())
        try:
            await engine.prepare(self.voice.voice)
            await render_voice(engine, "Готово.", self.voice)
        except Exception as e:
            if engine.offline and self.voice.engine == "piper":
                raise
            logger.warning("Голос {} пока недоступен: {}", self.voice.engine, e)
            if engine.offline:  # a missing local engine will not come back by itself
                self._unavailable.add(self.voice.engine)
                await self.engine("piper").prepare(self.offline_voice())
