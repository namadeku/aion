"""faster-whisper (CTranslate2). Uses CUDA when it actually works, otherwise CPU int8."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from loguru import logger

from aion import cuda, models
from aion.stt.base import SILENCE_DBFS, Pcm, SttEngine, drop_hallucinations, is_silence


@dataclass
class WhisperOptions:
    """Decoder tuning. All of it is optional in the config (see ``aion.stt.create_stt``)."""

    #: 0 = automatic: beam 5 on CUDA (cheap there, and it stops name-prompt loops on noise),
    #: greedy on the CPU (beam search would multiply the latency).
    beam_size: int = 0
    #: "auto" -> float16 on CUDA, int8 on CPU; or any CTranslate2 compute type.
    compute_type: str = "auto"
    #: CPU threads for CTranslate2 (0 = library default of 4).
    cpu_threads: int = 0
    #: How the assistant's names are given to the decoder:
    #: "prompt" - initial_prompt, "hotwords" - faster-whisper's hotwords, "none" - not at all.
    bias: Literal["prompt", "hotwords", "none"] = "prompt"
    #: Skip Whisper on clips quieter than this (dBFS): it only hallucinates on room noise.
    silence_dbfs: float = SILENCE_DBFS


class WhisperEngine(SttEngine):
    name = "whisper"

    def __init__(
        self,
        *args: Any,
        model: str = "auto",
        device: Literal["auto", "cpu", "cuda"] = "auto",
        data_dir: Path | None = None,
        options: WhisperOptions | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.model_name = model
        self.device = device
        self.data_dir = data_dir  # where on-demand CUDA libraries live (aion.cuda)
        self.options = options or WhisperOptions()
        self._model: Any = None
        self.active_device = "cpu"
        self.active_compute = "int8"

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
                self.active_compute = compute
                logger.info("Whisper {} загружен на {} ({})", name, device, compute)
                return model
            except Exception as e:  # no internet, missing CUDA libs, OOM, ...
                last_error = e
                logger.warning("Whisper на {} недоступен: {}", device, e)
        raise RuntimeError(f"Не удалось загрузить Whisper: {last_error}")

    def _open(self, path: Path, device: str, compute: str) -> Any:
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(path),
            device=device,
            compute_type=compute,
            cpu_threads=self.options.cpu_threads if device == "cpu" else 0,
        )
        # cuDNN/cuBLAS load lazily: run a tiny inference to be sure the device works.
        list(model.transcribe(np.zeros(16000, dtype=np.float32), language=self.language)[0])
        return model

    def _candidates(self) -> list[tuple[str, str]]:
        """Devices to try, in order, as (device, compute type).

        "auto" tries CUDA only when cuBLAS/cuDNN are present: the installer ships CPU only,
        and without the libraries the GPU attempt would first download large-v3-turbo
        (~1.6 GB) just to fail on a missing DLL. A failing explicit compute type falls back
        to the default one for the device.
        """
        wanted = self.options.compute_type
        candidates: list[tuple[str, str]] = []
        libraries = cuda.bundled() or (self.data_dir is not None and cuda.installed(self.data_dir))
        if self.device == "cuda" or (self.device == "auto" and libraries):
            cuda.add_dll_dirs(self.data_dir)
            if wanted != "auto":
                candidates.append(("cuda", wanted))
            candidates.append(("cuda", "float16"))
        elif self.device == "auto" and cuda.has_nvidia_gpu():
            logger.info(
                "Библиотеки CUDA не скачаны: Whisper работает на процессоре "
                "(скачать их можно на странице «Микрофон»)"
            )
        if self.device in ("auto", "cpu"):
            if wanted not in ("auto", "int8"):
                candidates.append(("cpu", wanted))
            candidates.append(("cpu", "int8"))
        return list(dict.fromkeys(candidates))

    async def warm_up(self) -> None:
        """First real inference is slower (kernel selection, caches): pay for it at start."""
        await self.load()
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(32000) * 2500).astype(np.int16)  # 2 s, loud enough to decode
        await asyncio.to_thread(self._run, noise, True)

    def _prompt(self) -> str | None:
        if self.options.bias != "prompt" or not self.hotwords:
            return None
        return ", ".join(self.hotwords) + "."

    def _run(self, audio: Pcm, force: bool = False) -> str:
        if not force and is_silence(audio, self.options.silence_dbfs):
            return ""
        samples = audio.astype(np.float32) / 32768.0
        hotwords = (
            " ".join(self.hotwords) if self.options.bias == "hotwords" and self.hotwords else None
        )
        segments, _ = self._model.transcribe(
            samples,
            language=self.language,
            beam_size=self.options.beam_size or (5 if self.active_device == "cuda" else 1),
            without_timestamps=True,
            initial_prompt=self._prompt(),
            hotwords=hotwords,
            condition_on_previous_text=False,
            # One temperature: the default fallback ladder re-decodes the clip up to six times
            # when the first pass looks odd, which is where multi-second latency spikes came from.
            temperature=0.0,
            vad_filter=False,
        )
        # Whisper's own "this was not speech" verdict (the usual thresholds of openai-whisper)
        spoken = (s for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0))
        return drop_hallucinations(" ".join(s.text.strip() for s in spoken).strip())

    async def transcribe(self, audio: Pcm) -> str:
        await self.load()
        return await asyncio.to_thread(self._run, audio)
