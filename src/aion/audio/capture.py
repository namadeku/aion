"""Microphone capture: 16 kHz mono int16 frames delivered to a thread-safe queue."""

from __future__ import annotations

import queue
from typing import Any

import numpy as np
from loguru import logger

from aion.audio.devices import resolve_device
from aion.audio.vad import FRAME, SAMPLE_RATE, Frame


class Microphone:
    def __init__(self, device: str | int | None = None, max_queue: int = 200) -> None:
        self.device = device
        self.frames: queue.Queue[Frame] = queue.Queue(maxsize=max_queue)
        self._stream: Any = None

    def start(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME,
            device=resolve_device(self.device, output=False),
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Микрофон открыт: {}", self._stream.device)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata: np.ndarray, frames: int, _time: Any, status: Any) -> None:
        if status:
            logger.debug("Микрофон: {}", status)
        frame = indata[:, 0].copy()
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            # The consumer is stalled: drop the oldest frame to stay real-time.
            try:
                self.frames.get_nowait()
                self.frames.put_nowait(frame)
            except (queue.Empty, queue.Full):
                pass
