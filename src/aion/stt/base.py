"""Speech-to-text backends. Input: 16 kHz mono int16."""

from __future__ import annotations

import re
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
    "субтитры подогнал",
    "субтитры предоставил",
    "субтитры добавил",
    "добавил субтитры",
    "редактор субтитров",
    "корректор а.",
    "семкин",
    "егорова",
    "dimatorzok",
    "продолжение следует",
    "спасибо за просмотр",
    "спасибо за внимание",
    "подписывайтесь на канал",
    "подписывайтесь на мой канал",
    "не забудьте подписаться",
    "ставьте лайки",
    "до встречи в следующем",
    "thanks for watching",
    "thank you for watching",
    "amara.org",
)

#: Peaks quieter than this (dBFS) mean a room, not a voice: Whisper would only dream.
#: Whispered speech sits around -40 dBFS peak; a quiet room's mic noise around -53.
SILENCE_DBFS = -46.0

_REPEATED_WORD = re.compile(r"\b([\w']+)\b(?:[\s,.!?…-]+\1\b){3,}", re.IGNORECASE)
_REPEATED_CHAR = re.compile(r"(.)\1{7,}")


def is_silence(audio: Pcm, floor_dbfs: float = SILENCE_DBFS) -> bool:
    """True if the clip is so quiet that anything recognized in it would be a hallucination.

    Judged by the 99.5th percentile of the amplitude (not RMS): a short quiet command inside
    a long pause has a tiny RMS but a clear peak.
    """
    if audio.size == 0:
        return True
    peak = float(np.percentile(np.abs(audio.astype(np.float32)), 99.5)) / 32768.0
    return 20.0 * float(np.log10(max(peak, 1e-6))) < floor_dbfs


def drop_hallucinations(text: str) -> str:
    """Empty string if the text is one of Whisper's typical made-up phrases or a decoding loop."""
    lowered = text.lower()
    if not any(ch.isalnum() for ch in lowered):
        return ""
    if any(marker in lowered for marker in HALLUCINATION_MARKERS):
        return ""
    if _REPEATED_WORD.search(lowered) or _REPEATED_CHAR.search(lowered):
        return ""  # "Да. Да. Да. Да." / "ааааааааа": the decoder is stuck in a loop
    return text


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

    async def warm_up(self) -> None:  # noqa: B027 - optional
        """Run one throwaway inference so the first real phrase does not pay for initialization."""

    @abstractmethod
    async def transcribe(self, audio: Pcm) -> str: ...
