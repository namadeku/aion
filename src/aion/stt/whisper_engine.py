"""faster-whisper (CTranslate2). Uses CUDA when it actually works, otherwise CPU int8."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import numpy as np
from loguru import logger

from aion import cuda, models
from aion.stt.base import Pcm, SttEngine, drop_hallucinations


class WhisperEngine(SttEngine):
    name = "whisper"

    def __init__(
        self,
        *args: Any,
        model: str = "auto",
        device: Literal["auto", "cpu", "cuda"] = "auto",
        data_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.model_name = model
        self.device = device
        self.data_dir = data_dir  # where on-demand CUDA libraries live (aion.cuda)
        self._model: Any = None
        self.active_device = "cpu"

    async def load(self) -> None:
        if self._model is None:
            self._model = await self._load()

    async def _load(self) -> Any:
        last_error: Exception | None = None
        for device, compute in self._candidates():
            name = self.model_name
            if name == "auto":
                name = "large-v3-turbo" if device == "cuda" else "small"
            try:
                path = await models.ensure_whisper(name, self.models_dir, self.progress)
                model = await asyncio.to_thread(self._open, path, device, compute)
                self.active_device = device
                logger.info("Whisper {} загружен на {} ({})", name, device, compute)
                return model
            except Exception as e:  # no internet, missing CUDA libs, OOM, ...
                last_error = e
                logger.warning("Whisper на {} недоступен: {}", device, e)
        raise RuntimeError(f"Не удалось загрузить Whisper: {last_error}")

    def _open(self, path: Path, device: str, compute: str) -> Any:
        from faster_whisper import WhisperModel

        model = WhisperModel(str(path), device=device, compute_type=compute)
        # cuDNN/cuBLAS load lazily: run a tiny inference to be sure the device works.
        list(model.transcribe(np.zeros(16000, dtype=np.float32), language=self.language)[0])
        return model

    def _candidates(self) -> list[tuple[str, str]]:
        """Devices to try, in order, as (device, compute type).

        "auto" tries CUDA only when cuBLAS/cuDNN are present: the installer ships CPU only,
        and without the libraries the GPU attempt would first download large-v3-turbo
        (~1.6 GB) just to fail on a missing DLL.
        """
        candidates: list[tuple[str, str]] = []
        libraries = cuda.bundled() or (self.data_dir is not None and cuda.installed(self.data_dir))
        if self.device == "cuda" or (self.device == "auto" and libraries):
            cuda.add_dll_dirs(self.data_dir)
            candidates.append(("cuda", "float16"))
        elif self.device == "auto" and cuda.has_nvidia_gpu():
            logger.info(
                "Библиотеки CUDA не скачаны: Whisper работает на процессоре "
                "(скачать их можно на странице «Микрофон»)"
            )
        if self.device in ("auto", "cpu"):
            candidates.append(("cpu", "int8"))
        return candidates

    async def transcribe(self, audio: Pcm) -> str:
        await self.load()
        samples = audio.astype(np.float32) / 32768.0
        prompt = ", ".join(self.hotwords) + "." if self.hotwords else None

        def run() -> str:
            segments, _ = self._model.transcribe(
                samples,
                language=self.language,
                beam_size=1,  # greedy: noticeably faster, same quality on short phrases
                without_timestamps=True,
                initial_prompt=prompt,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            # Whisper's own "this was not speech" verdict (the usual thresholds of openai-whisper)
            spoken = (s for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0))
            return drop_hallucinations(" ".join(s.text.strip() for s in spoken).strip())

        return await asyncio.to_thread(run)
