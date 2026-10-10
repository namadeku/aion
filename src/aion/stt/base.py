"""Speech-to-text backends. Input: 16 kHz mono int16."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

import numpy as np
import numpy.typing as npt

from aion.models import Progress

Pcm = npt.NDArray[np.int16]

# Whisper was trained on subtitled videos and "hears" their credits in noise and silence.
HALLUCINATION_MARKERS = (  # whole phrases: "включи субтитры" is a real command
    "субтитры сделал",
    "субтитры делал",
    "субтитры создавал",
    "добавил субтитры",
    "редактор субтитров",
    "dimatorzok",
    "продолжение следует",
    "спасибо за просмотр",
    "подписывайтесь на канал",
    "amara.org",
)


def drop_hallucinations(text: str) -> str:
    """Empty string if the text is one of Whisper's typical made-up phrases."""
    lowered = text.lower()
    return "" if any(marker in lowered for marker in HALLUCINATION_MARKERS) else text


class SttEngine(ABC):
    name: ClassVar[str]

    def __init__(self, models_dir: Path, language: str = "ru", progress: Progress | None = None):
        self.models_dir = models_dir
        self.language = language
        self.progress = progress
        #: Words to bias recognition towards (assistant names); engines may ignore it.
        self.hotwords: list[str] = []

    @abstractmethod
    async def load(self) -> None: ...

    @abstractmethod
    async def transcribe(self, audio: Pcm) -> str: ...
