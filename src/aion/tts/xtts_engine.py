"""Voice cloning with Coqui XTTS v2 (optional, heavy, GPU recommended).

Install: ``uv pip install coqui-tts``. The XTTS model license (CPML) is non-commercial.
The voice is cloned from ``voice.reference_wav`` (6-30 s of clean speech).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np

from aion.tts.base import Audio, TtsEngine, VoiceInfo

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"


class XttsEngine(TtsEngine):
    name = "xtts"
    reads_numbers = False

    def __init__(self, *args: Any, reference_wav: Path | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reference_wav = reference_wav
        self.language = "ru"
        self._tts: Any = None

    async def prepare(self, voice: str) -> None:
        await self._load()

    async def _load(self) -> Any:
        if self._tts is None:
            try:
                from TTS.api import TTS  # pyright: ignore[reportMissingImports]
            except ImportError as e:
                raise RuntimeError("Для клонирования голоса установите coqui-tts") from e

            def load() -> Any:
                import torch  # pyright: ignore[reportMissingImports]

                device = "cuda" if torch.cuda.is_available() else "cpu"
                return TTS(MODEL_NAME).to(device)

            self._tts = await asyncio.to_thread(load)
        return self._tts

    async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
        if self.reference_wav is None or not Path(self.reference_wav).exists():
            raise RuntimeError("Для XTTS укажите voice.reference_wav — образец голоса (WAV)")
        tts = await self._load()

        def run() -> Audio:
            wav = tts.tts(
                text=text, speaker_wav=str(self.reference_wav), language=self.language, speed=rate
            )
            return Audio(np.asarray(wav, dtype=np.float32), 24000)

        return await asyncio.to_thread(run)

    async def voices(self, language: str = "ru") -> list[VoiceInfo]:
        return [VoiceInfo("clone", "Клон по образцу WAV", language)]
