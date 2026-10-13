"""Post-processing of recognized speech before it reaches the router.

Whisper/Vosk produce the same slips again and again: compounds split in two ("скрин шот"),
brand names in the wrong script ("Chrome"), digits glued to units ("10минут"), spoken dates
as ordinals ("двадцатого марта"), "ё"/typographic noise. A small, explicit dictionary plus a
few regexes fixes them without any model; users extend it through ``stt.corrections``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Whole-phrase fixes (case-insensitive, word boundaries). Order matters: longer first.
PHRASE_FIXES: tuple[tuple[str, str], ...] = (
    (r"тайм\s*мер\w*", "таймер"),
    (r"тай\s+мер", "таймер"),
    (r"скрин\s+шот", "скриншот"),
    (r"скрин\s*шат", "скриншот"),
    (r"you\s*tube|ю\s+туб|ютьюб\w*", "ютуб"),
    (r"вай\s*фай", "вайфай"),
    (r"блю\s*тус\w*", "блютуз"),
    (r"телеграмм", "телеграм"),
    (r"дис\s*корд", "дискорд"),
    (r"спот\s*и\s*фай", "спотифай"),
    (r"гугл\s+хром|google\s+chrome", "хром"),
    (r"за\s+блокируй", "заблокируй"),
    (r"по\s+тише", "потише"),
    (r"по\s+громче", "погромче"),
    (r"сделает\s+(погромче|потише)", r"сделай \1"),
    (r"сделаю\s+(погромче|потише)", r"сделай \1"),
    (r"постав(?=\s+(?:таймер|будильник|на\s))", "поставь"),
)
_COMPILED = tuple((re.compile(rf"(?<!\w){p}(?!\w)", re.IGNORECASE), r) for p, r in PHRASE_FIXES)

_DIGIT_UNIT = re.compile(r"(\d)([а-яёa-z]{2,})", re.IGNORECASE)
_SPACES = re.compile(r"\s+")

_ORDINAL_UNITS = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "четвёртого": 4,
    "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9,
    "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13,
    "четырнадцатого": 14, "пятнадцатого": 15, "шестнадцатого": 16, "семнадцатого": 17,
    "восемнадцатого": 18, "девятнадцатого": 19,
}  # fmt: skip
_ORDINAL_TENS = {"двадцатого": 20, "тридцатого": 30}
_TENS_PREFIX = {"двадцать": 20, "тридцать": 30}
_MONTHS = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def ordinal_dates_to_digits(text: str) -> str:
    """ "двадцатого марта" -> "20 марта", "двадцать первого мая" -> "21 мая".

    Only genitive ordinals followed by a month name are converted: elsewhere ordinals are
    ordinary adjectives ("первого апреля" is a date, "первого человека" is not).
    """
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i].lower()
        nxt = words[i + 1].lower().strip(",.") if i + 1 < len(words) else ""
        nxt2 = words[i + 2].lower().strip(",.") if i + 2 < len(words) else ""
        if word in _TENS_PREFIX and nxt in _ORDINAL_UNITS and nxt2 in _MONTHS:
            out.append(str(_TENS_PREFIX[word] + _ORDINAL_UNITS[nxt]))
            i += 2
        elif nxt in _MONTHS and (word in _ORDINAL_UNITS or word in _ORDINAL_TENS):
            out.append(str(_ORDINAL_UNITS.get(word) or _ORDINAL_TENS[word]))
            i += 1
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def postprocess(text: str, corrections: Mapping[str, str] | None = None) -> str:
    """Clean up a recognition result. Idempotent; keeps the user's capitalization otherwise."""
    text = _SPACES.sub(" ", text.replace(" ", " ")).strip()
    if not text:
        return ""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    for wrong, right in (corrections or {}).items():
        if wrong.strip():
            text = re.sub(
                rf"(?<!\w){re.escape(wrong.strip())}(?!\w)", right, text, flags=re.IGNORECASE
            )
    text = _DIGIT_UNIT.sub(r"\1 \2", text)
    text = ordinal_dates_to_digits(text)
    return _SPACES.sub(" ", text).strip()
