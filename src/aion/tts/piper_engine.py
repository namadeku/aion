"""Piper TTS (fast, offline). Voices are downloaded from rhasspy/piper-voices on demand."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import numpy as np

from aion import models
from aion.tts.base import Audio, TtsEngine, VoiceInfo

# A curated list so the UI works offline; the full catalog is fetched when online.
KNOWN_VOICES = {
    "ru": [
        ("ru_RU-denis-medium", "Денис", "male"),
        ("ru_RU-dmitri-medium", "Дмитрий", "male"),
        ("ru_RU-ruslan-medium", "Руслан", "male"),
        ("ru_RU-irina-medium", "Ирина", "female"),
    ],
    "en": [
        ("en_GB-alan-medium", "Alan (British)", "male"),
        ("en_GB-northern_english_male-medium", "Northern English", "male"),
        ("en_US-ryan-high", "Ryan", "male"),
        ("en_US-lessac-medium", "Lessac", "female"),
    ],
}


class PiperEngine(TtsEngine):
    name = "piper"
    reads_numbers = False  # espeak reads digits, but not "°", "м/с" — normalize anyway

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._voices: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def prepare(self, voice: str) -> None:
        await self._load(voice)

    async def _load(self, voice: str) -> Any:
        async with self._lock:
            if voice in self._voices:
                return self._voices[voice]
            spec = models.piper_voice(voice)
            await models.ensure(spec, self.models_dir, self.progress)
            from piper import PiperVoice

            path = self.models_dir / "piper" / f"{voice}.onnx"
            loaded = await asyncio.to_thread(PiperVoice.load, path)
            self._voices[voice] = loaded
            return loaded

    async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
        piper_voice = await self._load(voice)
        from piper import SynthesisConfig

        config = SynthesisConfig(length_scale=1.0 / max(rate, 0.1))

        def run() -> Audio:
            chunks = list(piper_voice.synthesize(text, syn_config=config))
            if not chunks:
                return Audio(np.zeros(0, dtype=np.float32), 22050)
            samples = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
            return Audio(samples, chunks[0].sample_rate)

        return await asyncio.to_thread(run)

    async def voices(self, language: str = "ru") -> list[VoiceInfo]:
        installed = {p.stem for p in (self.models_dir / "piper").glob("*.onnx")}
        result = {
            vid: VoiceInfo(vid, title, language, gender, vid in installed)
            for vid, title, gender in KNOWN_VOICES.get(language, [])
        }
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                response = await client.get(f"{models.PIPER_BASE}/voices.json")
                catalog: dict[str, Any] = response.json()
            for vid, info in catalog.items():
                if info["language"]["family"] == language and vid not in result:
                    title = f"{info['name']} ({info['quality']})"
                    result[vid] = VoiceInfo(vid, title, language, "", vid in installed)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        return sorted(result.values(), key=lambda v: (not v.installed, v.id))
