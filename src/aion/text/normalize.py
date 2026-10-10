"""Text normalization for command matching: case, ё, punctuation and Russian numerals."""

from __future__ import annotations

import re

_UNITS = {
    "ноль": 0, "нуль": 0,
    "один": 1, "одна": 1, "одну": 1, "одно": 1, "раз": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}  # fmt: skip
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}  # fmt: skip
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}  # fmt: skip
# oblique cases ("до двадцати пяти", "из трёх", "на сорока"): genitive/dative/prepositional
_UNITS |= {
    "одного": 1, "одной": 1, "двух": 2, "трех": 3, "четырех": 4,
    **{word[:-1] + "и": value for word, value in _UNITS.items() if word.endswith("ь")},
}  # fmt: skip
_TENS |= {
    "двадцати": 20, "тридцати": 30, "сорока": 40, "пятидесяти": 50,
    "шестидесяти": 60, "семидесяти": 70, "восьмидесяти": 80, "девяноста": 90,
}  # fmt: skip
_HUNDREDS |= {"ста": 100}
_SCALES = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000, "тысячу": 1000,
           "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000}  # fmt: skip
_SPECIAL = {"полтора": "1.5", "полторы": "1.5", "пол": "0.5"}

# Words that are numerals only in a numeric context ("раз" = "once" is ambiguous).
_AMBIGUOUS = {"раз"}


def _is_number_word(word: str) -> bool:
    return word in _UNITS or word in _TENS or word in _HUNDREDS or word in _SCALES


def words_to_numbers(text: str) -> str:
    """Replace runs of Russian numeral words with digits: "двадцать пять минут" -> "25 минут"."""
    out: list[str] = []
    words = text.split()
    i = 0
    while i < len(words):
        word = words[i]
        if word in _SPECIAL:
            out.append(_SPECIAL[word])
            i += 1
            continue
        if not _is_number_word(word) or word in _AMBIGUOUS:
            out.append(word)
            i += 1
            continue
        total, current = 0, 0
        j = i
        while j < len(words) and _is_number_word(words[j]):
            w = words[j]
            if w in _SCALES:
                scale = _SCALES[w]
                total += (current or 1) * scale
                current = 0
            else:
                current += _UNITS.get(w) or _TENS.get(w) or _HUNDREDS.get(w, 0)
            j += 1
        out.append(str(total + current))
        i = j
    return " ".join(out)


_PUNCT_NOT_IN_NUMBER = re.compile(r"(?<!\d)[.,]|[.,](?!\d)")
_JUNK = re.compile(r"[^\w\s%+\-*/.,]")
_SPACES = re.compile(r"\s+")


def normalize(text: str, *, numbers: bool = True) -> str:
    """Lowercase, fold ё, drop punctuation, optionally convert numerals to digits."""
    text = text.lower().replace("ё", "е")
    text = _PUNCT_NOT_IN_NUMBER.sub(" ", text)
    text = _JUNK.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    if numbers:
        text = words_to_numbers(text)
    return text


def strip_wake_words(text: str, names: list[str]) -> str:
    """Remove the assistant name (and a following comma-pause) from the start of a phrase."""
    norm = text.strip()
    lowered = norm.lower().replace("ё", "е")
    for name in sorted(names, key=len, reverse=True):
        n = name.lower().replace("ё", "е")
        if lowered.startswith(n) and (len(lowered) == len(n) or not lowered[len(n)].isalnum()):
            return norm[len(n) :].lstrip(" ,.!?:;-—")
    return norm
