"""Wake-word detectors and fuzzy name spotting in recognized text."""

from __future__ import annotations

from abc import ABC, abstractmethod

from rapidfuzz import fuzz

from aion.audio.vad import Frame
from aion.text import normalize


class WakeWordDetector(ABC):
    """Streaming detector: fed 32 ms frames, returns the detected name or None."""

    @abstractmethod
    def process(self, frame: Frame) -> str | None: ...

    def reset(self) -> None:  # noqa: B027 - optional
        """Called at utterance boundaries."""

    def set_names(self, names: list[str]) -> None:  # noqa: B027 - optional
        """Assistant renamed: update what to listen for."""


def sensitivity_to_threshold(sensitivity: float) -> float:
    """0 -> strict (95), 0.5 -> 80, 1 -> lenient (65)."""
    return 95.0 - 30.0 * max(0.0, min(1.0, sensitivity))


def _squash(text: str) -> str:
    """Rough phonetic folding so "айон", "аион" and "a ион" compare as close."""
    text = normalize(text, numbers=False).replace(" ", "")
    for a, b in (("й", "и"), ("ё", "е"), ("ъ", ""), ("ь", ""), ("дж", "ж"), ("з", "с")):
        text = text.replace(a, b)
    return text


def find_name(text: str, names: list[str], threshold: float = 80.0) -> tuple[str, int] | None:
    """Find an assistant name among the first words of ``text``.

    Returns ``(name, words_consumed)``: the name may be split by the recognizer into several
    words ("а ион"), so windows of 1..3 words are compared to every name variant.
    """
    words = normalize(text, numbers=False).split()
    targets = [(n, _squash(n)) for n in names if n.strip()]
    best: tuple[float, str, int] | None = None
    for start in range(min(len(words), 4)):
        for size in (1, 2, 3):
            window = words[start : start + size]
            if len(window) < size:
                break
            candidate = _squash("".join(window))
            for name, squashed in targets:
                if abs(len(candidate) - len(squashed)) > max(2, len(squashed) // 2):
                    continue
                score = fuzz.ratio(candidate, squashed)
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, name, start + size)
        if best is not None:
            break
    return (best[1], best[2]) if best else None


def strip_name(text: str, names: list[str], threshold: float = 80.0) -> str:
    """Remove a (fuzzily matched) leading assistant name: "Айон, который час" -> "который час"."""
    found = find_name(text, names, threshold)
    if found is None:
        return text.strip()
    _, consumed = found
    original = text.split()
    return " ".join(original[consumed:]).lstrip(" ,.!?:;-—")
