"""Cuts a stream of VAD-scored frames into utterances (with pre-roll and hysteresis)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from aion.audio.vad import FRAME, SAMPLE_RATE, Frame


@dataclass(frozen=True)
class SpeechStart:
    pass


@dataclass(frozen=True)
class SpeechEnd:
    audio: npt.NDArray[np.int16]

    @property
    def duration(self) -> float:
        return len(self.audio) / SAMPLE_RATE


SegmentEvent = SpeechStart | SpeechEnd


class Segmenter:
    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_silence_ms: int = 600,
        min_speech_ms: int = 250,
        pre_roll_ms: int = 300,
        max_utterance_s: float = 15.0,
        start_frames: int = 2,
    ) -> None:
        frame_ms = FRAME * 1000 / SAMPLE_RATE
        self.threshold = threshold
        self.release = max(0.0, threshold - 0.15)
        self.silence_frames = max(1, round(min_silence_ms / frame_ms))
        self.min_speech_frames = max(1, round(min_speech_ms / frame_ms))
        self.max_frames = round(max_utterance_s * 1000 / frame_ms)
        self.start_frames = start_frames
        self._pre_roll: deque[Frame] = deque(maxlen=max(1, round(pre_roll_ms / frame_ms)))
        self._frames: list[Frame] = []
        self._in_speech = False
        self._voiced_run = 0
        self._silent_run = 0
        self._voiced_total = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def speech_seconds(self) -> float:
        """Length of the utterance being captured so far."""
        return len(self._frames) * FRAME / SAMPLE_RATE

    def current_audio(self) -> npt.NDArray[np.int16]:
        """Audio of the utterance being captured so far (for early wake-word checks)."""
        if not self._frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(self._frames).astype(np.int16)

    def reset(self) -> None:
        self._pre_roll.clear()
        self._frames = []  # a new list: _finish() keeps a reference to the old one
        self._in_speech = False
        self._voiced_run = self._silent_run = self._voiced_total = 0

    def push(self, frame: Frame, prob: float) -> SegmentEvent | None:
        if not self._in_speech:
            self._pre_roll.append(frame)
            self._voiced_run = self._voiced_run + 1 if prob >= self.threshold else 0
            if self._voiced_run >= self.start_frames:
                self._in_speech = True
                self._frames = list(self._pre_roll)
                self._pre_roll.clear()
                self._silent_run = 0
                self._voiced_total = self._voiced_run
                return SpeechStart()
            return None

        self._frames.append(frame)
        if prob >= self.release:
            self._silent_run = 0
            self._voiced_total += 1
        else:
            self._silent_run += 1
        if self._silent_run >= self.silence_frames or len(self._frames) >= self.max_frames:
            return self._finish()
        return None

    def flush(self) -> SpeechEnd | None:
        """Force-end the current utterance (e.g. push-to-talk released)."""
        return self._finish() if self._in_speech else None

    def _finish(self) -> SpeechEnd | None:
        frames, voiced = self._frames, self._voiced_total
        self.reset()
        if voiced < self.min_speech_frames:
            return SpeechEnd(np.zeros(0, dtype=np.int16))
        # drop most of the trailing silence, keep ~150 ms
        keep_tail = max(0, self.silence_frames - 5)
        if len(frames) > keep_tail:
            frames = frames[: len(frames) - keep_tail] if keep_tail else frames
        return SpeechEnd(np.concatenate(frames).astype(np.int16))
