"""Audio output: one persistent stream, interruptible playback, live output level for lip sync."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt
from loguru import logger

from aion.audio.devices import resolve_device

Samples = npt.NDArray[np.float32]
LevelCallback = Callable[[float], None]


def resample(samples: Samples, src_rate: int, dst_rate: int) -> Samples:
    if src_rate == dst_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    import soxr

    return soxr.resample(samples, src_rate, dst_rate).astype(np.float32, copy=False)


class AudioPlayer:
    def __init__(
        self,
        device: str | int | None = None,
        sample_rate: int = 48000,
        on_level: LevelCallback | None = None,
        blocksize: int = 960,  # 20 ms at 48 kHz
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.on_level = on_level
        self._stream: Any = None
        self._lock = threading.Lock()
        self._buffer: Samples = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._done: asyncio.Future[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._was_playing = False

    def start(self) -> None:
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            device=resolve_device(self.device, output=True),
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Вывод звука: {}", self._stream.device)

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._pos < len(self._buffer)

    async def play(self, samples: Samples, sample_rate: int) -> None:
        """Play and wait until finished. Cancelling the await stops playback immediately."""
        data = resample(samples, sample_rate, self.sample_rate)
        loop = asyncio.get_running_loop()
        done: asyncio.Future[None] = loop.create_future()
        with self._lock:
            self._finish_locked()
            self._buffer, self._pos, self._done = data, 0, done
        if self._stream is None:  # no device (tests): pretend playback took no time
            self.stop()
        try:
            await done
        except asyncio.CancelledError:
            self.stop()
            raise

    def stop(self) -> None:
        with self._lock:
            self._finish_locked()

    def _finish_locked(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._pos = 0
        done, self._done = self._done, None
        if done is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(_resolve, done)
        elif done is not None:
            _resolve(done)

    def _callback(self, outdata: np.ndarray, frames: int, _time: Any, status: Any) -> None:
        with self._lock:
            chunk = self._buffer[self._pos : self._pos + frames]
            self._pos += len(chunk)
            finished = self._done is not None and self._pos >= len(self._buffer)
            if finished:
                self._finish_locked()
        outdata[: len(chunk), 0] = chunk
        outdata[len(chunk) :, 0] = 0.0
        if self.on_level is not None:
            playing = len(chunk) > 0
            if playing or self._was_playing:
                rms = float(np.sqrt(np.mean(chunk * chunk))) if playing else 0.0
                self.on_level(min(1.0, rms * 4.0))
            self._was_playing = playing


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def chime(sample_rate: int = 48000, *, rising: bool = True) -> Samples:
    """A short two-tone "I'm listening" sound."""
    tones = (880.0, 1318.5) if rising else (1318.5, 880.0)
    parts: list[Samples] = []
    for freq in tones:
        t = np.arange(int(sample_rate * 0.07)) / sample_rate
        envelope = np.sin(np.pi * t / t[-1]) ** 2
        parts.append((0.25 * envelope * np.sin(2 * np.pi * freq * t)).astype(np.float32))
    return np.concatenate(parts)
