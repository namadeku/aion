"""Voice pipeline: microphone -> VAD -> wake word -> STT -> dialog, with barge-in.

Frame processing (VAD, segmentation, wake-word spotting) runs in a dedicated thread so the
event loop never stalls on audio work; results are posted to the loop as coroutines.

Modes:

* **passive** — waiting for the assistant's name; other speech is ignored (not transcribed);
* **active** — the next utterance is a command: after the name alone, during the follow-up
  window, while a plugin waits for an answer (``ctx.ask``) or after push-to-talk.

Barge-in (``audio.barge_in``): ``wake`` — saying the name while the assistant talks stops it;
``speech`` — any speech stops it (use headphones, otherwise it hears itself); ``off``.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
import time
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from loguru import logger

from aion.audio.capture import Microphone
from aion.audio.player import AudioPlayer, chime
from aion.audio.segmenter import Segmenter, SpeechEnd, SpeechStart
from aion.audio.vad import Vad, level
from aion.core.events import (
    AudioLevel,
    ConfigChanged,
    SpeechStarted,
    StateChanged,
    WakeDetected,
)
from aion.stt.base import SttEngine
from aion.wakeword.base import WakeWordDetector, find_name, sensitivity_to_threshold, strip_name

if TYPE_CHECKING:
    from aion.app import Aion

LEVEL_INTERVAL = 1 / 20  # input level events per second for visualizers
NAME_ONLY_LISTEN_S = 8.0
PUSH_TO_TALK_LISTEN_S = 10.0


class VoicePipeline:
    def __init__(
        self,
        app: Aion,
        mic: Microphone,
        vad: Vad,
        stt: SttEngine,
        wake: WakeWordDetector | None,
        *,
        player: AudioPlayer | None = None,
    ) -> None:
        self.app = app
        self.mic = mic
        self.vad = vad
        self.stt = stt
        self.wake = wake
        self.player = player
        vad_cfg = app.config.audio.vad
        self.segmenter = Segmenter(
            threshold=vad_cfg.threshold,
            min_silence_ms=vad_cfg.min_silence_ms,
            max_utterance_s=vad_cfg.max_utterance_s,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._active_until = 0.0
        self._listening = False
        self._expiry: asyncio.Task[None] | None = None
        self._barge_segment = False
        self._muted = False
        self._unsubscribe: list[Any] = []
        self._prefix_checks: dict[int, asyncio.Task[bool]] = {}
        #: Wake word is found by running the main STT over the start of each utterance.
        self.stt_wake = wake is None and app.config.wakeword.backend == "stt"
        self._update_names()

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.stt.hotwords = self._names
        await self.stt.load()
        self._running.set()
        self._thread = threading.Thread(target=self._worker, name="aion-audio", daemon=True)
        self._thread.start()
        self.mic.start()
        bus = self.app.bus
        self._unsubscribe = [
            bus.subscribe(ConfigChanged, self._on_config_changed),
            bus.subscribe(StateChanged, self._on_state_changed),
        ]
        logger.info("Голосовой ввод запущен, имя: {}", ", ".join(self._names))

    async def stop(self) -> None:
        self._running.clear()
        self.mic.stop()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 2.0)
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._deactivate()

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """Pause listening entirely (privacy toggle in the UI/tray)."""
        self._muted = muted
        if muted:
            self._deactivate()

    # -- activation -----------------------------------------------------------------------

    def activate(self, seconds: float = PUSH_TO_TALK_LISTEN_S) -> None:
        """Treat the next utterance as a command (no name needed) for ``seconds``."""
        self._active_until = max(self._active_until, time.monotonic() + seconds)
        if not self._listening:
            self._listening = True
            self.app.state.raise_flag("listening")
        if self._expiry is None or self._expiry.done():
            self._expiry = asyncio.get_running_loop().create_task(self._expire())

    async def push_to_talk(self) -> None:
        if self.app.state.state == "speaking" or self.app.dialog.busy:
            await self.app.dialog.interrupt("hotkey")
        self._play_chime()
        self.activate()

    def _deactivate(self) -> None:
        self._active_until = 0.0
        if self._listening:
            self._listening = False
            self.app.state.lower_flag("listening")

    async def _expire(self) -> None:
        while True:
            remaining = self._active_until - time.monotonic()
            if remaining <= 0:
                if self.segmenter.in_speech:  # let the current utterance finish
                    await asyncio.sleep(0.2)
                    continue
                self._deactivate()
                return
            await asyncio.sleep(remaining)

    def _is_active(self) -> bool:
        dialog = self.app.dialog
        return (
            time.monotonic() < self._active_until or dialog.awaiting_answer or dialog.in_follow_up
        )

    # -- worker thread --------------------------------------------------------------------

    def _post(self, coro: Coroutine[Any, Any, None]) -> None:
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(_log_failure)

    def _worker(self) -> None:
        woken = False
        started_while_speaking = False
        segment = 0
        prefix_checked = False
        last_level = 0.0
        barge_in = self.app.config.audio.barge_in
        while self._running.is_set():
            try:
                frame = self.mic.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._muted:
                continue
            now = time.monotonic()
            if now - last_level >= LEVEL_INTERVAL:
                last_level = now
                assert self._loop is not None
                self.app.bus.emit_threadsafe(
                    AudioLevel(level=level(frame), channel="input"), self._loop
                )

            speaking = self.app.state.state == "speaking"
            try:
                prob = self.vad(frame)
            except Exception:
                logger.exception("Ошибка VAD")
                continue

            wake_allowed = not speaking or barge_in == "wake"
            if self.wake is not None and not woken and wake_allowed:
                try:
                    name = self.wake.process(frame)
                except Exception:
                    logger.exception("Ошибка wake word")
                    name = None
                if name:
                    woken = True
                    self._post(self._on_wake(name))

            event = self.segmenter.push(frame, prob)
            if isinstance(event, SpeechStart):
                segment += 1
                prefix_checked = False
                started_while_speaking = speaking
                self._post(self._on_speech_start(speaking))
            elif isinstance(event, SpeechEnd):
                if self.wake is not None:
                    self.wake.reset()
                self._post(self._on_utterance(segment, event.audio, woken, started_while_speaking))
                woken = started_while_speaking = False
                barge_in = self.app.config.audio.barge_in
            elif (
                self.stt_wake
                and self.segmenter.in_speech
                and not prefix_checked
                and wake_allowed
                and self.segmenter.speech_seconds >= self.app.config.wakeword.prefix_seconds
                and not self._is_active()
            ):
                # Early check: does this (still ongoing) utterance start with the name?
                prefix_checked = True
                self._post(self._start_prefix_check(segment, self.segmenter.current_audio()))

    # -- event handlers (event loop) ------------------------------------------------------

    async def _on_wake(self, name: str) -> None:
        logger.info("Wake word: {}", name)
        await self.app.bus.publish(WakeDetected(word=name))
        if self.app.state.state == "speaking" or self.app.dialog.busy:
            await self.app.dialog.interrupt("wake")
        self.activate(NAME_ONLY_LISTEN_S)

    async def _start_prefix_check(self, segment: int, audio: npt.NDArray[np.int16]) -> None:
        self._prefix_checks[segment] = asyncio.create_task(self._prefix_has_name(audio))

    async def _prefix_has_name(self, audio: npt.NDArray[np.int16]) -> bool:
        text = await self.stt.transcribe(audio)
        found = find_name(text, self._names, self._threshold())
        logger.debug("Проверка имени по началу фразы: {!r} -> {}", text, found)
        if found is not None:
            await self._on_wake(found[0])
            return True
        return False

    async def _on_speech_start(self, while_speaking: bool) -> None:
        await self.app.bus.publish(SpeechStarted())
        self._barge_segment = False
        if while_speaking and self.app.config.audio.barge_in == "speech":
            self._barge_segment = True
            await self.app.dialog.interrupt("speech")

    async def _on_utterance(
        self,
        segment: int,
        audio: npt.NDArray[np.int16],
        woken: bool,
        started_while_speaking: bool,
    ) -> None:
        check = self._prefix_checks.pop(segment, None)
        if check is not None:
            woken = await check or woken
        if audio.size == 0:
            return
        text: str | None = None
        if not (woken or self._barge_segment or self._is_active()):
            # Not addressed to us — unless a short phrase (never prefix-checked) has the name.
            if not self.stt_wake or check is not None or started_while_speaking:
                return
            text = await self.stt.transcribe(audio)
            found = find_name(text, self._names, self._threshold())
            if found is None:
                return
            woken = True
            await self._on_wake(found[0])
        elif started_while_speaking and not (woken or self._barge_segment):
            return  # most likely our own voice from the speakers
        self._deactivate()
        if text is None:
            with self.app.state.active("thinking"):
                started = time.perf_counter()
                text = await self.stt.transcribe(audio)
                logger.debug(
                    "STT {:.2f} с для {:.1f} с речи: {!r}",
                    time.perf_counter() - started,
                    len(audio) / 16000,
                    text,
                )
        text = self._strip_name(text, force=woken)
        if not text:
            if woken:  # just the name: "I'm listening"
                self._play_chime()
                self.activate(NAME_ONLY_LISTEN_S)
            return
        await self.app.dialog.submit(text, source="voice")

    def _threshold(self) -> float:
        return sensitivity_to_threshold(self.app.config.wakeword.sensitivity)

    def _strip_name(self, text: str, *, force: bool) -> str:
        threshold = self._threshold()
        if force or find_name(text, self._names, threshold):
            return strip_name(text, self._names, threshold)
        return text.strip()

    async def _on_state_changed(self, event: StateChanged) -> None:
        if event.new == "idle" and self.app.dialog.in_follow_up:
            remaining = self.app.dialog.follow_up_until - time.monotonic()
            if remaining > 0:
                self.activate(remaining)

    def _on_config_changed(self, event: ConfigChanged) -> None:
        if {"profile", "profiles"} & set(event.sections):
            self._update_names()
            if self.wake is not None:
                self.wake.set_names(self._names)
            self.stt.hotwords = self._names
            logger.info("Новое имя для активации: {}", ", ".join(self._names))

    def _update_names(self) -> None:
        self._names = self.app.config.profile.wake_names()

    def _play_chime(self) -> None:
        if self.player is None:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self.player.play(chime(), 48000))


def _log_failure(future: Any) -> None:
    if not future.cancelled() and (exc := future.exception()) is not None:
        logger.opt(exception=exc).error("Ошибка в голосовом конвейере")
