"""Speech synthesis backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import numpy as np

from aion.audio.player import Samples
from aion.models import Progress


@dataclass(frozen=True)
class Audio:
    samples: Samples  # mono float32 in [-1, 1]
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    name: str
    language: str
    gender: str = ""
    installed: bool = True


class TtsEngine(ABC):
    name: ClassVar[str]
    offline: ClassVar[bool] = True
    #: The engine handles digits/symbols itself (otherwise text is normalized first).
    reads_numbers: ClassVar[bool] = False

    def __init__(self, models_dir: Path, progress: Progress | None = None) -> None:
        self.models_dir = models_dir
        self.progress = progress

    async def prepare(self, voice: str) -> None:  # noqa: B027 - optional
        """Download/load what ``voice`` needs (called at startup and on voice change)."""

    @abstractmethod
    async def synthesize(self, text: str, *, voice: str, rate: float = 1.0) -> Audio:
        """``rate`` > 1 speaks faster."""

    @abstractmethod
    async def voices(self, language: str = "ru") -> list[VoiceInfo]: ...


def silence(seconds: float, sample_rate: int = 22050) -> Audio:
    return Audio(np.zeros(int(seconds * sample_rate), dtype=np.float32), sample_rate)
