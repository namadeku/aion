"""Silero TTS (very natural Russian voices). Needs the optional ``silero`` extra (torch).

Note: Silero models are licensed CC BY-NC-SA 4.0 (non-commercial).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np

from aion.tts.base import Audio, TtsEngine, VoiceInfo

MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
SPEAKERS = [
    ("aidar", "Айдар", "male"),
    ("eugene", "Евгений", "male"),
    ("baya", "Бая", "female"),
    ("kseniya", "Ксения", "female"),
    ("xenia", "Ксения 2", "female"),
]
SAMPLE_RATE = 48000


class SileroEngine(TtsEngine):
    name = "silero"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._model: Any = None
        self._lock = asyncio.Lock()

    async def prepare(self, voice: str) -> None:
        await self._load()

    async def _load(self) -> Any:
        async with self._lock:
            if self._model is not None:
                return self._model
            try:
                import torch  # pyright: ignore[reportMissingImports]
            except ImportError as e:
                raise RuntimeError("Для Silero установите extra: uv sync --extra silero") from e
            from aion import models

            spec = models.ModelSpec(
                "silero-tts-ru",
                "Silero TTS ru v4",
                (models.ModelFile(MODEL_URL, Path("silero") / "v4_ru.pt"),),
            )
            await models.ensure(spec, self.models_dir, self.progress)
            path = self.models_dir / "silero" / "v4_ru.pt"

            def load() -> Any:
                from torch.package.package_importer import PackageImporter

                model = PackageImporter(str(path)).load_pickle("tts_models", "model")
                model.to(torch.device("cpu"))
                return model

            self._model = await asyncio.to_thread(load)
            return self._model

    async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
        model = await self._load()
        speaker = voice if voice in {s for s, _, _ in SPEAKERS} else "aidar"
        speed = "medium" if abs(rate - 1) < 0.1 else ("fast" if rate > 1 else "slow")
        escaped = text.replace("&", " и ").replace("<", " ").replace(">", " ")
        ssml = f'<speak><prosody rate="{speed}">{escaped}</prosody></speak>'

        def run() -> Audio:
            audio = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=SAMPLE_RATE)
            return Audio(np.asarray(audio, dtype=np.float32), SAMPLE_RATE)

        return await asyncio.to_thread(run)

    async def voices(self, language: str = "ru") -> list[VoiceInfo]:
        if language != "ru":
            return []
        return [VoiceInfo(s, title, "ru", gender) for s, title, gender in SPEAKERS]
