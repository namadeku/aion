"""Voice pipeline: microphone -> VAD -> wake word -> STT -> dialog, with barge-in.

Frame processing (VAD, segmentation, wake-word spotting) runs in a dedicated thread so the
event loop never stalls on audio work; results are posted to the loop as coroutines.

Modes:

* **passive** — waiting for the assistant's name; other speech is ignored (not transcribed);
* **active** — the next utterance is a command: after the name alone, during the follow-up
  window, while a plugin waits for an answer (``ctx.ask``) or after push-to-talk.

Barge-in (``audio.barge_in``): ``wake`` — saying the name while the assistant talks stops it;
``speech`` — any speech stops it (use headphones, otherwise it hears itself); ``off``.

Echo: while the assistant talks its voice reaches the microphone. Utterances that *start* during
playback are ignored unless the name opens them; right after playback (room reverb) they are
transcribed, and dropped only if the transcript is made of the assistant's own words
(:class:`aion.audio.echo.EchoGuard`).

Latency: with ``audio.speculative_ms`` the recognizer starts after a short pause inside a phrase
and its result is used if the phrase then really ends (see ``SpeechPause``).
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
from aion.audio.dsp import prepare_for_stt
from aion.audio.echo import EchoGuard
from aion.audio.player import AudioPlayer, chime
from aion.audio.segmenter import Segmenter, SpeechEnd, SpeechPause, SpeechStart
from aion.audio.tuning import VoiceTuning
from aion.audio.vad import SAMPLE_RATE, Vad, level
from aion.core.events import (
    AudioLevel,
    ConfigChanged,
    SpeechStarted,
    StateChanged,
    TtsFinished,
    TtsStarted,
    WakeDetected,
)
from aion.stt.base import SttEngine
from aion.text.asr import postprocess
from aion.wakeword.base import TAIL_THRESHOLD, WakeWordDetector, sensitivity_to_threshold
from aion.wakeword.names import NameMatch, match_name

if TYPE_CHECKING:
    from aion.app import Aion

LEVEL_INTERVAL = 1 / 20  # input level events per second for visualizers
NAME_ONLY_LISTEN_S = 8.0
PUSH_TO_TALK_LISTEN_S = 10.0

# What the assistant was doing when an utterance began
QUIET, ECHO_TAIL, SPEAKING = 0, 1, 2


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
        self.tuning = VoiceTuning.from_config(app.config)
        self.segmenter = Segmenter()
        self._configure_segmenter()
        self.echo = EchoGuard(self.tuning.echo_holdoff_s)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._active_until = 0.0
        # until when the next utterance counts as addressed by name (the name was said alone
        # or push-to-talk was pressed): user commands in prefix mode need it
        self._named_until = 0.0
        self._listening = False
        self._expiry: asyncio.Task[None] | None = None
        self._barge_segment = False
        self._muted = False
        self._unsubscribe: list[Any] = []
        self._prefix_checks: dict[int, asyncio.Task[bool]] = {}
        self._speculative: dict[int, asyncio.Task[str | None]] = {}
        #: Wake word is found by running the main STT over the start of each utterance.
        self.stt_wake = wake is None and app.config.wakeword.backend == "stt"
        self._update_names()

    def _configure_segmenter(self) -> None:
        vad_cfg = self.app.config.audio.vad
        self.segmenter.configure(
            threshold=vad_cfg.threshold,
            min_silence_ms=vad_cfg.min_silence_ms,
            max_utterance_s=vad_cfg.max_utterance_s,
            pause_ms=self.tuning.speculative_ms,
        )

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.stt.hotwords = self._names
        await self.stt.load()
        await self.stt.warm_up()
        self._running.set()
        self._thread = threading.Thread(target=self._worker, name="aion-audio", daemon=True)
        self._thread.start()
        self.mic.start()
        bus = self.app.bus
        self._unsubscribe = [
            bus.subscribe(ConfigChanged, self._on_config_changed),
            bus.subscribe(StateChanged, self._on_state_changed),
            bus.subscribe(TtsStarted, self._on_tts_started),
            bus.subscribe(TtsFinished, self._on_tts_finished),
        ]
        logger.info("Голосовой ввод запущен, имя: {}", ", ".join(self._names))

    async def stop(self) -> None:
        self._running.clear()
        self.mic.stop()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 2.0)
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        for task in self._speculative.values():
            task.cancel()
        self._speculative.clear()
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
        self._named_until = time.monotonic() + PUSH_TO_TALK_LISTEN_S
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

    def _phase(self) -> int:
        if self.app.state.state == "speaking":
            return SPEAKING
        return ECHO_TAIL if self.echo.speaking() else QUIET

    def _worker(self) -> None:  # noqa: PLR0912, PLR0915
        woken = False
        started_phase = QUIET
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

            phase = self._phase()
            try:
                prob = self.vad(frame)
            except Exception:
                logger.exception("Ошибка VAD")
                continue

            wake_allowed = phase == QUIET or barge_in == "wake"
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
                started_phase = phase
                self._post(self._on_speech_start(phase == SPEAKING))
            elif isinstance(event, SpeechPause):
                self._post(self._on_pause(segment, event.audio, woken, started_phase))
            elif isinstance(event, SpeechEnd):
                if self.wake is not None:
                    self.wake.reset()
                self._post(
                    self._on_utterance(
                        segment, event.audio, woken, started_phase, event.after_pause
                    )
                )
                woken = False
                started_phase = QUIET
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

    # -- recognition ----------------------------------------------------------------------

    async def recognize(self, audio: npt.NDArray[np.int16]) -> str:
        """Condition the audio, run the STT and clean up the text."""
        tuning = self.tuning
        if tuning.normalize or tuning.denoise:
            audio = await asyncio.to_thread(
                prepare_for_stt, audio, normalize=tuning.normalize, denoise=tuning.denoise
            )
        text = await self.stt.transcribe(audio)
        if text and tuning.postprocess:
            text = postprocess(text, tuning.corrections)
        return text

    # -- event handlers (event loop) ------------------------------------------------------

    async def _on_wake(self, name: str) -> None:
        logger.info("Wake word: {}", name)
        await self.app.bus.publish(WakeDetected(word=name))
        if self.app.state.state == "speaking" or self.app.dialog.busy:
            await self.app.dialog.interrupt("wake")
        self._named_until = time.monotonic() + NAME_ONLY_LISTEN_S
        self.activate(NAME_ONLY_LISTEN_S)

    async def _start_prefix_check(self, segment: int, audio: npt.NDArray[np.int16]) -> None:
        self._prefix_checks[segment] = asyncio.create_task(self._prefix_has_name(audio))

    async def _prefix_has_name(self, audio: npt.NDArray[np.int16]) -> bool:
        text = await self.recognize(audio)
        found = self._locate_name(text, allow_tail=False)
        logger.debug("Проверка имени по началу фразы: {!r} -> {}", text, found)
        if found is None:
            return False
        if self.echo.is_echo(text):
            logger.debug("Имя услышано в собственной речи, пропускаю: {!r}", text)
            return False
        await self._on_wake(found.name)
        return True

    async def _on_speech_start(self, while_speaking: bool) -> None:
        await self.app.bus.publish(SpeechStarted())
        self._barge_segment = False
        if while_speaking and self.app.config.audio.barge_in == "speech":
            self._barge_segment = True
            await self.app.dialog.interrupt("speech")

    async def _on_pause(
        self, segment: int, audio: npt.NDArray[np.int16], woken: bool, phase: int
    ) -> None:
        """A short pause inside a phrase: start recognizing now if the result will be needed."""
        stale = self._speculative.pop(segment, None)
        if stale is not None:
            stale.cancel()
        check = self._prefix_checks.get(segment)
        addressed = woken or self._barge_segment or self._is_active()
        if phase == SPEAKING and not addressed and check is None:
            return
        if not (addressed or check is not None or self.stt_wake):
            return
        self._speculative[segment] = asyncio.create_task(self._speculate(audio, check, addressed))

    async def _speculate(
        self, audio: npt.NDArray[np.int16], check: asyncio.Task[bool] | None, addressed: bool
    ) -> str | None:
        if check is not None and not addressed:
            found = await check
            if not found and not self.tail_name_wanted(len(audio) / SAMPLE_RATE):
                return None
        return await self.recognize(audio)

    async def _text_for(
        self, segment: int, audio: npt.NDArray[np.int16], *, after_pause: bool
    ) -> str:
        """The transcript of the utterance: the speculative one if it is still valid."""
        spec = self._speculative.pop(segment, None)
        if spec is not None:
            if after_pause:
                with contextlib.suppress(Exception):
                    text = await spec
                    if text is not None:
                        return text
            else:
                spec.cancel()
        return await self.recognize(audio)

    async def _on_utterance(
        self,
        segment: int,
        audio: npt.NDArray[np.int16],
        woken: bool,
        started_phase: int,
        after_pause: bool = False,
    ) -> None:
        check = self._prefix_checks.pop(segment, None)
        if check is not None:
            woken = await check or woken
        if audio.size == 0:
            spec = self._speculative.pop(segment, None)
            if spec is not None:
                spec.cancel()
            return
        text: str | None = None
        if not (woken or self._barge_segment or self._is_active()):
            # Not addressed to us — unless the name is in the phrase: a short one (never
            # prefix-checked) or a mid-length one with the name at the end.
            if not self.stt_wake or started_phase == SPEAKING:
                return
            if check is not None and not self._tail_check_wanted(audio):
                return
            text = await self._text_for(segment, audio, after_pause=after_pause)
            found = self._locate_name(text)
            if found is None or self.echo.is_echo(text):
                return
            woken = True
            await self._on_wake(found.name)
        elif started_phase == SPEAKING and not (woken or self._barge_segment):
            return  # most likely our own voice from the speakers
        self._deactivate()
        if text is None:
            with self.app.state.active("thinking"):
                started = time.perf_counter()
                text = await self._text_for(segment, audio, after_pause=after_pause)
                logger.debug(
                    "STT {:.2f} с для {:.1f} с речи: {!r}",
                    time.perf_counter() - started,
                    len(audio) / SAMPLE_RATE,
                    text,
                )
        if started_phase == ECHO_TAIL and not woken and self.echo.is_echo(text):
            logger.debug("Эхо собственной речи: {!r}", text)
            return
        text = self._strip_name(text)
        if not text:
            if woken:  # just the name: "I'm listening"
                self._play_chime()
                self.activate(NAME_ONLY_LISTEN_S)
            return
        named = woken or time.monotonic() < self._named_until
        self._named_until = 0.0
        await self.app.dialog.submit(text, source="voice", named=named)

    def _tail_check_wanted(self, audio: npt.NDArray[np.int16]) -> bool:
        """After a negative start check: is the phrase short enough to look for a trailing name?"""
        return self.tail_name_wanted(len(audio) / SAMPLE_RATE)

    def tail_name_wanted(self, seconds: float) -> bool:
        limit = self.tuning.tail_check_max_s
        if limit <= 0:
            limit = 8.0 if getattr(self.stt, "active_device", "cpu") == "cuda" else 4.0
        return self.tuning.tail_name and seconds <= limit

    # -- names ----------------------------------------------------------------------------

    def _threshold(self) -> float:
        return sensitivity_to_threshold(self.app.config.wakeword.sensitivity)

    def _locate_name(self, text: str, *, allow_tail: bool = True) -> NameMatch | None:
        """The name opening ``text`` or, for a known command, closing it."""
        use_tail = allow_tail and self.tuning.tail_name
        found = match_name(
            text,
            self._names,
            self._threshold(),
            tail_threshold=TAIL_THRESHOLD if use_tail else None,
        )
        if found is None or found.where == "head":
            return found
        # "Меня зовут Аион" is not a call: a trailing name only counts after a command
        known = self.app.router.match(text[: found.char_start]) is not None
        return found if known else None

    def _strip_name(self, text: str) -> str:
        found = self._locate_name(text)
        if found is None:
            return text.strip()
        if found.where == "tail":
            return text[: found.char_start].rstrip(" ,.!?:;-—")
        return text[found.char_end :].lstrip(" ,.!?:;-—")

    # -- bus handlers ---------------------------------------------------------------------

    async def _on_state_changed(self, event: StateChanged) -> None:
        if event.new == "idle" and self.app.dialog.in_follow_up:
            remaining = self.app.dialog.follow_up_until - time.monotonic()
            if remaining > 0:
                self.activate(remaining)

    async def _on_tts_started(self, event: TtsStarted) -> None:
        self.echo.started(event.text)

    async def _on_tts_finished(self, event: TtsFinished) -> None:
        self.echo.finished(event.text)

    def _on_config_changed(self, event: ConfigChanged) -> None:
        sections = set(event.sections)
        if {"audio", "stt", "wakeword"} & sections:
            self.tuning = VoiceTuning.from_config(self.app.config)
            self.echo.holdoff_s = self.tuning.echo_holdoff_s
            self._configure_segmenter()
        if {"profile", "profiles"} & sections:
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
