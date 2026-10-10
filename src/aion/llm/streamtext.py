"""Turning a token stream into speakable sentences.

* :class:`ThinkFilter`   — drops ``<think>…</think>`` blocks of reasoning models;
* :class:`EmotionTag`    — extracts ``[joy]``-style emotion tags (leading or trailing);
* :class:`SentenceSplitter` — emits complete sentences as soon as they are ready so speech
  synthesis starts long before the model finishes;
* :func:`clean_for_speech` — strips markdown that should not be read aloud.
"""

from __future__ import annotations

import re

EMOTIONS = {"neutral", "joy", "thinking", "surprise", "sad", "angry"}
_TAG_WORD = re.compile(r"[\w-]{1,20}")
_LATIN_TAG = re.compile(r"[a-z_-]{2,20}")


class ThinkFilter:
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._inside = False
        self._buffer = ""

    def feed(self, text: str) -> str:
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            if self._inside:
                end = self._buffer.find(self.CLOSE)
                if end < 0:
                    # keep a possible partial closing tag
                    self._buffer = self._buffer[-(len(self.CLOSE) - 1) :]
                    break
                self._buffer = self._buffer[end + len(self.CLOSE) :]
                self._inside = False
            else:
                start = self._buffer.find(self.OPEN)
                if start < 0:
                    keep = _partial_suffix(self._buffer, self.OPEN)
                    out.append(self._buffer[: len(self._buffer) - keep])
                    self._buffer = self._buffer[len(self._buffer) - keep :]
                    break
                out.append(self._buffer[:start])
                self._buffer = self._buffer[start + len(self.OPEN) :]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        rest, self._buffer = ("" if self._inside else self._buffer), ""
        return rest


def _partial_suffix(text: str, tag: str) -> int:
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


class EmotionTag:
    """Pulls ``[joy]``-style emotion tags out of the stream, wherever they are.

    The prompt asks for the tag at the *end* of the answer: a leading tag makes a small model
    start talking instead of calling a tool. A leading tag still works. Unknown word tags
    (``[timer]``) are dropped only at the very start, where they are model noise. ``emotion``
    is the latest tag seen; the caller resets it after applying.
    """

    MAX_TAG = 22  # a "[" with no "]" this far is just a bracket

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted = False
        self.emotion: str | None = None

    def feed(self, text: str) -> str:
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            start = self._buffer.find("[")
            if start < 0:
                out.append(self._buffer)
                self._buffer = ""
                break
            out.append(self._buffer[:start])
            self._buffer = self._buffer[start:]
            end = self._buffer.find("]")
            if end < 0:
                if len(self._buffer) <= self.MAX_TAG:
                    break  # wait for the rest of a possible tag
                out.append(self._buffer[0])
                self._buffer = self._buffer[1:]
                continue
            tag = self._buffer[1:end].strip().lower()
            leading = not self._emitted and not "".join(out).strip()
            # a made-up latin tag ("[curious]") is never meant to be read aloud
            droppable = tag in EMOTIONS or leading or _LATIN_TAG.fullmatch(tag)
            if _TAG_WORD.fullmatch(tag) and droppable:
                if tag in EMOTIONS:
                    self.emotion = tag
                self._buffer = self._buffer[end + 1 :]
                if leading:
                    self._buffer = self._buffer.lstrip()
            else:
                out.append(self._buffer[0])
                self._buffer = self._buffer[1:]
        result = "".join(out)
        if result.strip():
            self._emitted = True
        return result

    def flush(self) -> str:
        rest, self._buffer = self._buffer, ""
        return rest


_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)]*\s+|\n+")
_ABBREVIATIONS = (
    "т.е.",
    "т.к.",
    "т.д.",
    "т.п.",
    "др.",
    "г.",
    "гг.",
    "см.",
    "им.",
    "e.g.",
    "i.e.",
    "mr.",
    "dr.",
)


_CLAUSE_END = re.compile(r"(?<=[,;:])\s+|\s+[—–-]\s+")


class SentenceSplitter:
    """Emits complete sentences.

    ``first_clause_chars`` lets the *first* phrase end at a comma/dash once it is at least that
    long: speech synthesis starts on "Знаешь, это хороший вопрос," instead of waiting for the
    whole first sentence.
    """

    def __init__(
        self, min_chars: int = 12, max_chars: int = 220, first_clause_chars: int = 0
    ) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.first_clause_chars = first_clause_chars
        self._buffer = ""
        self._emitted = False

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        sentences: list[str] = []
        start = 0
        for m in _SENTENCE_END.finditer(self._buffer):
            newline = "\n" in m.group(0)
            if not newline:
                if m.end() >= len(self._buffer):
                    break  # the next character decides; wait for more tokens
                following = self._buffer[m.end()]
                if following.islower() or following.isdigit():
                    continue  # "5 окт. 2026 г. прохладно": an abbreviation, not an end
            candidate = self._buffer[start : m.start()].strip()
            if len(candidate) < self.min_chars and "\n" not in m.group(0):
                continue
            if candidate.lower().endswith(_ABBREVIATIONS):
                continue
            if candidate:
                sentences.append(candidate)
            start = m.end()
        self._buffer = self._buffer[start:]
        while len(self._buffer) > self.max_chars:  # very long sentence: cut at a comma/space
            window = self._buffer[: self.max_chars]
            cut = max(window.rfind(", "), window.rfind(" "))
            if cut <= self.min_chars:
                break
            sentences.append(self._buffer[: cut + 1].strip())
            self._buffer = self._buffer[cut + 1 :]
        if not sentences and not self._emitted and self.first_clause_chars:
            for m in _CLAUSE_END.finditer(self._buffer):
                if m.start() >= self.first_clause_chars and m.end() < len(self._buffer):
                    sentences.append(self._buffer[: m.start()].strip())
                    self._buffer = self._buffer[m.end() :]
                    break
        out = [s for s in (clean_for_speech(s) for s in sentences) if s]
        self._emitted = self._emitted or bool(out)
        return out

    def flush(self) -> list[str]:
        rest, self._buffer = clean_for_speech(self._buffer), ""
        return [rest] if rest else []


_MARKDOWN = (
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"(?<!\w)[*_](.+?)[*_](?!\w)"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"https?://\S+"), "ссылка"),
    # emoji and pictographs: speech synthesis would read "улыбающееся лицо" or garble them
    (re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿️‍]+"), ""),
)


def clean_for_speech(text: str) -> str:
    for pattern, repl in _MARKDOWN:
        text = pattern.sub(repl, text)
    return re.sub(r"\s+", " ", text).strip()
