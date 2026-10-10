"""Speech-to-text engines."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aion.config import Config
    from aion.models import Progress
    from aion.stt.base import SttEngine


def create_stt(config: Config, progress: Progress | None = None) -> SttEngine:
    stt = config.stt
    language = config.assistant.language
    if stt.backend == "vosk":
        from aion.stt.vosk_engine import VoskEngine

        return VoskEngine(config.paths.models, language, progress, model=stt.vosk_model)
    from aion.stt.whisper_engine import WhisperEngine

    return WhisperEngine(
        config.paths.models,
        language,
        progress,
        model=stt.whisper_model,
        device=stt.device,
        data_dir=config.paths.data_dir,
    )
