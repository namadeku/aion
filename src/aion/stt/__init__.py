"""Speech-to-text engines."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aion.config import Config
    from aion.models import Progress
    from aion.stt.base import SttEngine
    from aion.stt.whisper_engine import WhisperOptions


def tuning(section: object, name: str, default: Any) -> Any:
    """An optional config field: the schema may not define it (yet), then ``default`` is used."""
    return getattr(section, name, default)


def whisper_options(config: Config) -> WhisperOptions:
    from aion.stt.whisper_engine import WhisperOptions

    stt = config.stt
    base = WhisperOptions()
    return WhisperOptions(
        beam_size=int(tuning(stt, "beam_size", base.beam_size)),
        compute_type=str(tuning(stt, "compute_type", base.compute_type)),
        cpu_threads=int(tuning(stt, "cpu_threads", base.cpu_threads)),
        bias=tuning(stt, "bias", base.bias),
        silence_dbfs=float(tuning(stt, "silence_dbfs", base.silence_dbfs)),
    )


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
        options=whisper_options(config),
    )
