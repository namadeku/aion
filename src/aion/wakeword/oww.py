"""openWakeWord backend (pretrained or custom-trained ONNX models, e.g. "hey_jarvis").

Optional: ``uv pip install openwakeword``. Much cheaper than streaming STT, but the word is
fixed by the model — renaming the assistant does not change it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from aion.audio.vad import Frame
from aion.wakeword.base import WakeWordDetector

CHUNK = 1280  # 80 ms, what openWakeWord expects


class OpenWakeWord(WakeWordDetector):
    def __init__(self, model: str, sensitivity: float = 0.5, models_dir: Path | None = None):
        try:
            import openwakeword  # pyright: ignore[reportMissingImports]
            from openwakeword.model import Model  # pyright: ignore[reportMissingImports]
        except ImportError as e:
            raise RuntimeError("Установите openwakeword: uv pip install openwakeword") from e
        custom = models_dir / "openwakeword" / f"{model}.onnx" if models_dir else None
        if custom is not None and custom.exists():
            self._model: Any = Model(wakeword_models=[str(custom)], inference_framework="onnx")
        else:
            openwakeword.utils.download_models([model])
            self._model = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = 0.8 - 0.6 * max(0.0, min(1.0, sensitivity))  # 0.5 -> 0.5
        self._buffer = np.zeros(0, dtype=np.int16)

    def reset(self) -> None:
        self._model.reset()
        self._buffer = np.zeros(0, dtype=np.int16)

    def process(self, frame: Frame) -> str | None:
        self._buffer = np.concatenate([self._buffer, frame])
        detected: str | None = None
        while len(self._buffer) >= CHUNK:
            chunk, self._buffer = self._buffer[:CHUNK], self._buffer[CHUNK:]
            scores: dict[str, float] = self._model.predict(chunk)
            for name, score in scores.items():
                if score >= self.threshold:
                    detected = name
        if detected:
            self.reset()
        return detected
