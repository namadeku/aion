"""Fuzzy spotting of the assistant's name in recognized Russian text.

Whisper/Vosk spell an unusual name differently every time ("Аион", "Айон", "а ион", "Aion",
"Эйон"). Matching works on a phonetic *fold*: Latin is transliterated, voiced consonants are
devoiced, unstressed vowels are reduced (о/а, е/и/э, я/а ...), so all these spellings collapse to
the same string; the remaining distance is Levenshtein-based (rapidfuzz).

False triggers are the real enemy of an always-listening assistant, so three guards apply:

* the name must open the phrase (after at most two filler words: "слушай, Аион, ...");
  a name later in a sentence ("меня зовут Аион") is not a call;
* real words that sound close to a name ("ион", "лион", "неон", "они") never match unless they
  are spelled exactly like one of the configured names;
* a trailing name ("который час, Аион") needs a much higher similarity than a leading one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from aion.text import normalize

#: Words that may precede the name: "слушай, Аион", "эй, Аион", "ну Аион".
FILLERS = frozenset(
    (
    "слушай", "слушайте", "послушай", "эй", "ну", "так", "окей", "ок", "ладно", "алло", "хей",
    "хэй", "привет", "здравствуй", "а", "о", "ой", "ах", "эх", "вот", "да", "внимание",
    )
)  # fmt: skip

#: Ordinary words that fold close to the default names. Rejected unless they are a configured name.
CONFUSABLE = frozenset(
    (
    "ион", "иона", "иону", "ионы", "ионов", "эон", "эона", "эоны", "лион", "лиона", "неон",
    "неона", "мион", "пион", "дион", "айрон", "он", "она", "они", "оно", "оны", "оня", "яна",
    "ян", "иван", "алло", "аня",
    )
)  # fmt: skip

#: Function words that are never the first half of a split name ("я и он" is not "Аион").
_NOT_NAME_START = frozenset(
    (
    "я", "и", "в", "с", "к", "у", "о", "на", "не", "за", "по", "до", "из", "от", "мы", "вы",
    "ты", "он", "она", "они", "но", "да", "же", "бы", "ли", "то", "что", "как", "это", "вот",
    "все", "так", "уже", "или", "для", "при", "про", "над", "под", "без",
    )
)  # fmt: skip

_LATIN = str.maketrans(
    {
        "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
        "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
        "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
        "y": "й", "z": "з",
    }
)  # fmt: skip
_FOLD = str.maketrans(
    {
        "ё": "а", "о": "а", "я": "а", "э": "и", "е": "и", "ы": "и", "й": "и", "ю": "у",
        "б": "п", "в": "ф", "г": "к", "д": "т", "ж": "ш", "з": "с", "ъ": "", "ь": "",
    }
)  # fmt: skip
_TOKEN = re.compile(r"[\w'-]+|[,.!?;:…—]")

Where = Literal["head", "tail"]


@dataclass(frozen=True)
class NameMatch:
    name: str
    score: float
    where: Where
    #: words of the phrase covered by the match (inclusive start, exclusive end)
    start: int
    end: int
    #: the same span as character offsets in the original text
    char_start: int
    char_end: int


@dataclass(frozen=True)
class _Word:
    text: str
    start: int
    end: int
    after_break: bool  # punctuation separates it from the previous word


def fold(text: str) -> str:
    """Phonetic skeleton of a word or phrase: spelling variants of one sound compare equal."""
    text = "".join(ch for ch in text.lower().translate(_LATIN) if ch.isalpha())
    text = text.translate(_FOLD)
    out: list[str] = []
    for ch in text:
        if not out or out[-1] != ch:  # "аионн" -> "аион"
            out.append(ch)
    return "".join(out)


def _plain(text: str) -> str:
    return "".join(ch for ch in normalize(text, numbers=False) if ch.isalpha())


def _tokenize(text: str) -> list[_Word]:
    words: list[_Word] = []
    pending = False
    for m in _TOKEN.finditer(text.lower().replace("ё", "е")):
        token = m.group()
        if token[0].isalnum():
            words.append(_Word(token.strip("'-"), m.start(), m.end(), pending))
            pending = False
        else:
            pending = True
    return words


def _score(candidate_words: list[str], targets: list[tuple[str, str, str]]) -> tuple[float, str]:
    raw = "".join(candidate_words)
    folded = fold(raw)
    if not folded:
        return 0.0, ""
    best, best_name = 0.0, ""
    for name, plain, target in targets:
        if raw in CONFUSABLE and raw != plain:
            continue
        if abs(len(folded) - len(target)) > max(2, len(target) // 2):
            continue
        if folded != target and folded[0] != target[0]:
            continue  # a different first sound is a different word ("леона" is not "аион")
        # an exact spelling beats a phonetic equivalent: the log shows the name actually heard
        score = 100.0 if raw == plain else min(99.5, float(fuzz.ratio(folded, target)))
        if score > best:
            best, best_name = score, name
    return best, best_name


def _joinable(first: _Word, second: _Word) -> bool:
    """May two recognizer words be two halves of one name ("а ион")?"""
    return not second.after_break and first.text not in _NOT_NAME_START


def _match(words: list[_Word], first: int, size: int, where: Where, score: float, name: str):
    last = words[first + size - 1]
    return NameMatch(name, score, where, first, first + size, words[first].start, last.end)


def match_name(
    text: str,
    names: list[str],
    threshold: float = 80.0,
    *,
    tail_threshold: float | None = None,
) -> NameMatch | None:
    """The configured name at the start of ``text`` (or, if allowed, at its very end).

    ``tail_threshold`` enables the trailing form; it should be stricter than ``threshold``.
    A name split by the recognizer ("а ион") is joined only when no punctuation separates the
    pieces, so "Эй, он опять..." is not read as "Эйон".
    """
    words = _tokenize(text)
    targets = [(n, _plain(n), fold(n)) for n in names if fold(n)]
    if not words or not targets:
        return None
    for start in range(min(len(words), 3)):
        if start and words[start - 1].text not in FILLERS:
            break
        best: NameMatch | None = None
        for size in (1, 2):
            if start + size > len(words):
                break
            if size == 2 and not _joinable(words[start], words[start + 1]):
                continue
            score, name = _score([w.text for w in words[start : start + size]], targets)
            if score >= threshold and (best is None or score > best.score):
                best = _match(words, start, size, "head", score, name)
        if best is not None:
            return best
    if tail_threshold is not None and len(words) >= 2:
        best = None
        for size in (1, 2):
            first = len(words) - size
            if first < 1 or (size == 2 and not _joinable(words[first], words[first + 1])):
                continue
            score, name = _score([w.text for w in words[first:]], targets)
            if score >= tail_threshold and (best is None or score > best.score):
                best = _match(words, first, size, "tail", score, name)
        return best
    return None
