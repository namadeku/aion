"""Vosk (Kaldi) — lightweight offline recognition; also powers the wake-word spotter."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from aion import models
from aion.models import Progress
from aion.stt.base import Pcm, SttEngine

_model_cache: dict[Path, Any] = {}
_cache_lock = threading.Lock()


def default_vosk_model(language: str) -> str:
    return "vosk-model-small-en-us-0.15" if language == "en" else "vosk-model-small-ru-0.22"


async def load_vosk_model(models_dir: Path, name: str, progress: Progress | None = None) -> Any:
    """Download (once) and load a Vosk model; loaded models are shared between users."""
    await models.ensure(models.vosk_model(name), models_dir, progress)
    path = models_dir / "vosk" / name

    def load() -> Any:
        import vosk

        vosk.SetLogLevel(-1)
        with _cache_lock:
            if path not in _model_cache:
                _model_cache[path] = vosk.Model(str(path))
            return _model_cache[path]

    return await asyncio.to_thread(load)


class VoskEngine(SttEngine):
    name = "vosk"

    def __init__(self, *args: Any, model: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.model_name = model or default_vosk_model(self.language)
        self._model: Any = None

    async def load(self) -> None:
        if self._model is None:
            self._model = await load_vosk_model(self.models_dir, self.model_name, self.progress)

    async def transcribe(self, audio: Pcm) -> str:
        await self.load()

        def run() -> str:
            import vosk

            recognizer = vosk.KaldiRecognizer(self._model, 16000)
            recognizer.AcceptWaveform(audio.tobytes())
            return str(json.loads(recognizer.FinalResult()).get("text", "")).strip()

        return await asyncio.to_thread(run)
