"""Text-level echo guard: is what the microphone heard just the assistant's own voice?

Without acoustic echo cancellation the assistant hears itself through the speakers, and its own
sentences ("Меня зовут Аион...") can contain the wake name. We know exactly what was being said
(``TtsStarted``/``TtsFinished``), so a transcript made mostly of words from recent assistant
phrases is echo, not a user.
"""

from __future__ import annotations

import time
from collections import deque

from rapidfuzz import fuzz

from aion.text import normalize

_NEVER = float("inf")
#: Fraction of heard words that must come from the assistant's own phrases to call it echo.
ECHO_SHARE = 0.7


class EchoGuard:
    def __init__(self, holdoff_s: float = 0.6, window_s: float = 4.0, memory: int = 6) -> None:
        #: room reverb and device buffers: the mic still hears the voice this long after it ended
        self.holdoff_s = holdoff_s
        #: how long after the last phrase a transcript is still compared with it
        self.window_s = window_s
        self._phrases: deque[tuple[list[str], float]] = deque(maxlen=memory)
        self._busy_until = 0.0

    def started(self, text: str) -> None:
        self._phrases.append((normalize(text).split(), _NEVER))

    def finished(self, text: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        words = normalize(text).split()
        for i, (phrase, _) in enumerate(self._phrases):
            if phrase == words:
                self._phrases[i] = (phrase, now)
        self._busy_until = now + self.holdoff_s

    def speaking(self, now: float | None = None) -> bool:
        """The assistant is talking, or has only just stopped (the room still rings)."""
        now = time.monotonic() if now is None else now
        return now < self._busy_until or any(end == _NEVER for _, end in self._phrases)

    def is_echo(self, heard: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        recent = [w for words, end in self._phrases if now - end < self.window_s for w in words]
        heard_words = normalize(heard).split()
        if not recent or not heard_words:
            return False
        known = sum(1 for w in heard_words if any(_same(w, r) for r in set(recent)))
        return known / len(heard_words) >= ECHO_SHARE


def _same(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and fuzz.ratio(a, b) >= 80)
