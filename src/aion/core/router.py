"""Command router: exact template matching first, fuzzy matching (rapidfuzz) second.

Pattern syntax (matched against normalized text, see :func:`aion.text.normalize`):

* ``{city}``         — a slot; integer/float slots (by handler annotation) match numbers only;
* ``(включи|запусти)`` — alternatives;
* ``[пожалуйста]``    — optional words, ``[громкость|звук]`` — optional alternatives.

A pattern also matches when the phrase has leading filler ("аион, скажи который час" matches
``который час``), with a small score penalty per extra word.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from aion.text import normalize

_TOKEN = re.compile(r"\{(\w+)\}|\(([^()]+)\)|\[([^\[\]]+)\]|([^{}()\[\]]+)")
_NUMBER = r"-?\d+(?:[.,]\d+)?"
_MAX_EXPANSIONS = 32


@dataclass(frozen=True, eq=False)
class CommandSpec:
    plugin: str
    name: str
    patterns: tuple[str, ...]
    handler: Callable[..., Awaitable[Any]]
    slot_types: dict[str, type] = field(default_factory=dict)
    dangerous: bool = False
    priority: int = 0
    description: str = ""


@dataclass(frozen=True)
class Match:
    command: CommandSpec
    pattern: str
    score: float
    slots: dict[str, str]


@dataclass(frozen=True)
class _Compiled:
    command: CommandSpec
    source: str
    exact: re.Pattern[str]
    loose: re.Pattern[str]
    literal_words: int
    variants: tuple[str, ...]  # literal expansions for fuzzy matching (slot-free patterns)
    prefix_variants: tuple[str, ...]  # literal prefixes for "literal {slot}" patterns
    tail_slot: str | None


def _norm(text: str) -> str:
    return normalize(text, numbers=False)


def _literal(text: str) -> str:
    return r"\s+".join(re.escape(w) for w in normalize(text, numbers=False).split())


def _compile(command: CommandSpec, source: str) -> _Compiled:
    parts: list[str] = []
    expansions: list[list[str]] = []
    slots: list[str] = []
    for slot, alt, optional, text in _TOKEN.findall(source):
        if slot:
            kind = command.slot_types.get(slot)
            body = _NUMBER if kind in (int, float) else r".+?"
            parts.append(rf"(?P<{slot}>{body})")
            slots.append(slot)
            expansions.append(["{" + slot + "}"])
        elif alt:
            options = [_norm(o) for o in alt.split("|")]
            parts.append("(?:" + "|".join(_literal(o) for o in options) + ")")
            expansions.append(options)
        elif optional:
            options = [o.strip() for o in optional.split("|")]
            parts.append("(?:" + "|".join(_literal(o) for o in options) + ")?")
            expansions.append([*(_norm(o) for o in options), ""])
        elif text.strip():
            parts.append(_literal(text))
            expansions.append([_norm(text)])
    body = r"\s*".join(p for p in parts if p)
    variants = [
        " ".join(w for w in combo if w)
        for combo in itertools.islice(itertools.product(*expansions), _MAX_EXPANSIONS)
    ]
    tail_slot = None
    prefix_variants: tuple[str, ...] = ()
    if len(slots) == 1 and source.rstrip().endswith("}"):
        tail_slot = slots[0]
        prefix_variants = tuple(v.removesuffix("{" + tail_slot + "}").strip() for v in variants)
    return _Compiled(
        command=command,
        source=source,
        exact=re.compile(rf"^{body}$"),
        loose=re.compile(rf"^(?:(?P<_prefix>.*?)\s+)?{body}$"),
        literal_words=max(
            (sum(1 for w in v.split() if not w.startswith("{")) for v in variants), default=0
        ),
        variants=tuple(v for v in variants if "{" not in v) if not slots else (),
        prefix_variants=prefix_variants,
        tail_slot=tail_slot,
    )


class Router:
    def __init__(self, fuzzy_threshold: int = 82) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self._compiled: list[_Compiled] = []

    def set_commands(self, commands: list[CommandSpec]) -> None:
        self._compiled = [_compile(c, p) for c in commands for p in c.patterns]

    @property
    def commands(self) -> list[CommandSpec]:
        return list(dict.fromkeys(c.command for c in self._compiled))

    def match(self, text: str) -> Match | None:
        """The best match above the threshold."""
        ranked = self.match_all(text)
        return ranked[0] if ranked else None

    def match_all(self, text: str) -> list[Match]:
        """All matches above the threshold, best first, one per command.

        Ranking: score, then priority, then the number of literal words (an exact phrase
        beats a template with a slot that happens to swallow the same words).
        """
        norm = normalize(text)
        if not norm:
            return []
        best: dict[CommandSpec, tuple[tuple[float, int, int], Match]] = {}
        for compiled in self._compiled:
            found = self._match_one(compiled, norm)
            if found is None or found.score < self.fuzzy_threshold:
                continue
            key = (found.score, compiled.command.priority, compiled.literal_words)
            current = best.get(compiled.command)
            if current is None or key > current[0]:
                best[compiled.command] = (key, found)
        return [m for _, m in sorted(best.values(), key=lambda item: item[0], reverse=True)]

    def _match_one(self, c: _Compiled, norm: str) -> Match | None:
        if m := c.exact.match(norm):
            return Match(c.command, c.source, 100.0, _slots(m))
        if m := c.loose.match(norm):
            extra = len((m.group("_prefix") or "").split())
            return Match(c.command, c.source, 97.0 - 2.0 * extra, _slots(m))
        words = norm.split()
        if c.variants:
            score = max(_fuzzy(v, norm, len(words)) for v in c.variants)
            return Match(c.command, c.source, score, {})
        if c.tail_slot and c.prefix_variants:
            return self._fuzzy_tail_slot(c, words)
        return None

    @staticmethod
    def _fuzzy_tail_slot(c: _Compiled, words: list[str]) -> Match | None:
        """Fuzzy-match "literal {slot}" patterns: compare the leading words, rest is the slot."""
        best: Match | None = None
        for prefix in c.prefix_variants:
            n = len(prefix.split())
            if n == 0 or len(words) <= n:
                continue
            score = float(fuzz.ratio(prefix, " ".join(words[:n])))
            slot_value = " ".join(words[n:])
            kind = c.command.slot_types.get(c.tail_slot or "")
            if kind in (int, float) and not re.fullmatch(_NUMBER, slot_value):
                continue
            if best is None or score > best.score:
                best = Match(c.command, c.source, score, {c.tail_slot or "": slot_value})
        return best


def _fuzzy(variant: str, norm: str, words: int) -> float:
    """Token-set similarity penalized by the word count difference, below an exact match.

    Token-set ratio is 100 whenever one phrase's words are a subset of the other's, so
    "что у меня на экране" would fully match "прочитай что у меня на экране" too — both
    extra and missing words cost points.
    """
    diff = abs(words - len(variant.split()))
    by_set = fuzz.token_set_ratio(variant, norm) - 4.0 * diff
    return min(99.0, max(by_set, float(fuzz.ratio(variant, norm))))


def _slots(m: re.Match[str]) -> dict[str, str]:
    return {k: v.strip() for k, v in m.groupdict().items() if k != "_prefix" and v is not None}
