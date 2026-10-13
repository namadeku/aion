"""Wake-word detectors and fuzzy name spotting in recognized text."""

from __future__ import annotations

from abc import ABC, abstractmethod

from aion.audio.vad import Frame
from aion.wakeword.names import match_name


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


TAIL_THRESHOLD = 90.0  # a name after the command ("который час, Аион") must be near-exact


def find_name(
    text: str, names: list[str], threshold: float = 80.0, *, tail: bool = False
) -> tuple[str, int] | None:
    """Find an assistant name opening ``text`` (see :mod:`aion.wakeword.names`).

    Returns ``(name, words_consumed)``: the name may be split by the recognizer into several
    words ("а ион"), and filler words before it ("слушай, Аион") count as consumed.
    With ``tail=True`` a (near-exact) name closing the phrase is accepted as well.
    """
    found = match_name(text, names, threshold, tail_threshold=TAIL_THRESHOLD if tail else None)
    return (found.name, found.end) if found else None


def strip_name(text: str, names: list[str], threshold: float = 80.0, *, tail: bool = False) -> str:
    """Remove a (fuzzily matched) assistant name: "Айон, который час" -> "который час"."""
    found = match_name(text, names, threshold, tail_threshold=TAIL_THRESHOLD if tail else None)
    if found is None:
        return text.strip()
    if found.where == "tail":
        return text[: found.char_start].rstrip(" ,.!?:;-—")
    return text[found.char_end :].lstrip(" ,.!?:;-—")
