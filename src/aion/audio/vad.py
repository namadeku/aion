"""Voice activity detection on 16 kHz int16 frames of 512 samples (32 ms).

Backends: Silero VAD v5 via onnxruntime (no torch), WebRTC VAD, plain energy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import numpy.typing as npt

FRAME = 512
SAMPLE_RATE = 16000

Frame = npt.NDArray[np.int16]


class Vad(ABC):
    @abstractmethod
    def __call__(self, frame: Frame) -> float:
        """Speech probability 0..1 for one frame."""

    def reset(self) -> None:  # noqa: B027 - optional
        pass


class SileroVad(Vad):
    _CONTEXT = 64

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._CONTEXT), dtype=np.float32)

    def __call__(self, frame: Frame) -> float:
        x = (frame.astype(np.float32) / 32768.0).reshape(1, -1)
        x = np.concatenate([self._context, x], axis=1)
        out, state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._state = np.asarray(state, dtype=np.float32)
        self._context = x[:, -self._CONTEXT :]
        return float(np.asarray(out).reshape(-1)[0])


class WebRtcVad(Vad):
    def __init__(self, aggressiveness: int = 2) -> None:
        import webrtcvad

        self._vad = webrtcvad.Vad(aggressiveness)

    def __call__(self, frame: Frame) -> float:
        # WebRTC accepts 10/20/30 ms frames: use the first 30 ms (480 samples).
        return 1.0 if self._vad.is_speech(frame[:480].tobytes(), SAMPLE_RATE) else 0.0


class EnergyVad(Vad):
    """Loudness-based fallback: maps -50..-20 dBFS to 0..1."""

    def __init__(self, floor_db: float = -50.0, ceiling_db: float = -20.0) -> None:
        self.floor_db = floor_db
        self.ceiling_db = ceiling_db

    def __call__(self, frame: Frame) -> float:
        db = rms_db(frame)
        return float(np.clip((db - self.floor_db) / (self.ceiling_db - self.floor_db), 0.0, 1.0))


def rms_db(frame: Frame) -> float:
    x = frame.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
    return 20.0 * np.log10(max(rms, 1e-6))


def level(frame: Frame) -> float:
    """Loudness 0..1 for visualizers."""
    return float(np.clip((rms_db(frame) + 60.0) / 60.0, 0.0, 1.0))
