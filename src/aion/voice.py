"""Assembling the voice stack (TTS output, VAD, wake word, STT, pipeline) from the config."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from aion import models
from aion.audio.capture import Microphone
from aion.audio.pipeline import VoicePipeline
from aion.audio.player import AudioPlayer
from aion.audio.vad import EnergyVad, SileroVad, Vad, WebRtcVad
from aion.config import Config, ConfigStore
from aion.core import EventBus, SpeechOutput, StateMachine
from aion.core.events import AudioLevel
from aion.models import Progress
from aion.stt import create_stt
from aion.tts.output import VoiceOutput
from aion.wakeword.base import WakeWordDetector

if TYPE_CHECKING:
    from aion.app import Aion

OUTPUT_LEVEL_INTERVAL = 1 / 30


def voice_output_factory(
    store: ConfigStore, progress: Progress | None = None
) -> Callable[[EventBus, StateMachine, Config], SpeechOutput]:
    """Speech factory for :class:`aion.app.Aion` that speaks through the speakers."""

    def factory(bus: EventBus, state: StateMachine, config: Config) -> SpeechOutput:
        loop = asyncio.get_event_loop()
        last = [0.0]

        def on_level(value: float) -> None:  # audio thread
            now = time.monotonic()
            if now - last[0] >= OUTPUT_LEVEL_INTERVAL or value == 0.0:
                last[0] = now
                bus.emit_threadsafe(AudioLevel(level=value, channel="output"), loop)

        player = AudioPlayer(config.audio.output_device, on_level=on_level)
        return VoiceOutput(bus, state, store, player, progress)

    return factory


async def create_vad(config: Config, progress: Progress | None = None) -> Vad:
    backend = config.audio.vad.backend
    if backend == "silero":
        try:
            await models.ensure(models.silero_vad(), config.paths.models, progress)
            return SileroVad(config.paths.models / "silero_vad.onnx")
        except Exception as e:
            logger.warning("Silero VAD недоступен ({}), использую WebRTC", e)
            backend = "webrtc"
    if backend == "webrtc":
        try:
            return WebRtcVad()
        except Exception as e:
            logger.warning("WebRTC VAD недоступен ({}), использую энергетический", e)
    return EnergyVad()


async def create_wake(config: Config, progress: Progress | None = None) -> WakeWordDetector | None:
    ww = config.wakeword
    if ww.backend in ("none", "stt"):
        return None  # "stt" is handled by the pipeline with the main STT engine
    if ww.backend == "openwakeword":
        from aion.wakeword.oww import OpenWakeWord

        return await asyncio.to_thread(
            OpenWakeWord, ww.openwakeword_model, ww.sensitivity, config.paths.models
        )
    from aion.stt.vosk_engine import default_vosk_model, load_vosk_model
    from aion.wakeword.vosk_spotter import VoskSpotter

    name = config.stt.vosk_model or default_vosk_model(config.assistant.language)
    model = await load_vosk_model(config.paths.models, name, progress)
    return VoskSpotter(model, config.profile.wake_names(), ww.sensitivity)


async def create_pipeline(app: Aion, progress: Progress | None = None) -> VoicePipeline:
    config = app.config
    vad, wake = await asyncio.gather(create_vad(config, progress), create_wake(config, progress))
    stt = create_stt(config, progress)
    player = app.speech.player if isinstance(app.speech, VoiceOutput) else None
    mic = Microphone(config.audio.input_device)
    return VoicePipeline(app, mic, vad, stt, wake, player=player)
