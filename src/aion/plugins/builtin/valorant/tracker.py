"""Turns the player's Valorant presence (from the Riot Client local API) into observations.

The presence is what friends see in the client: game phase, map, queue and the score of
the party owner's match. It is coarse (no kills), but enough for match start/end and rounds.
Riot has changed its layout over time, so fields are looked up both at the top level and in
the nested ``*PresenceData`` blocks.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from aion.core.initiative import Observation
from aion.sdk.games import RoundStreak

Presence = dict[str, Any]

MAPS = {
    "Ascent": "Ascent",
    "Bonsai": "Split",
    "Canyon": "Fracture",
    "Duality": "Bind",
    "Foxtrot": "Breeze",
    "Infinity": "Abyss",
    "Jam": "Lotus",
    "Juliett": "Sunset",
    "Pitt": "Pearl",
    "Port": "Icebox",
    "Range": "стрельбище",
    "Triad": "Haven",
}

# rounds needed to win, for queues where the score counts rounds
ROUNDS_TO_WIN = {"competitive": 13, "unrated": 13, "premier": 13, "swiftplay": 5, "spikerush": 4}

_NESTED = ("matchPresenceData", "partyPresenceData", "playerPresenceData")


def decode_private(private: str) -> Presence:
    """The ``private`` field of a presence is base64-encoded JSON."""
    try:
        data = json.loads(base64.b64decode(private))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def field(presence: Presence, name: str) -> Any:
    if name in presence:
        return presence[name]
    for block in _NESTED:
        nested = presence.get(block)
        if isinstance(nested, dict) and name in nested:
            return nested[name]  # pyright: ignore[reportUnknownVariableType]
    return None


def map_name(path: Any) -> str:
    """``/Game/Maps/Bonsai/Bonsai`` -> ``Split``."""
    code = str(path or "").rstrip("/").rsplit("/", 1)[-1]
    return MAPS.get(code, code or "неизвестной карте")


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


class ValorantTracker:
    def __init__(self) -> None:
        self.phase: str | None = None  # MENUS / PREGAME / INGAME
        self.score = (0, 0)  # ally, enemy
        self.queue = ""
        self.map = ""
        self.streak = RoundStreak()

    def update(self, presence: Presence) -> list[Observation]:
        phase = field(presence, "sessionLoopState")
        if not isinstance(phase, str):
            return []
        old, self.phase = self.phase, phase
        score = (
            _int(field(presence, "partyOwnerMatchScoreAllyTeam")),
            _int(field(presence, "partyOwnerMatchScoreEnemyTeam")),
        )
        old_score, self.score = self.score, score
        if phase != "MENUS":
            self.queue = str(field(presence, "queueId") or "")
            self.map = map_name(field(presence, "matchMap"))
        if old is None or self.map == "стрельбище":
            return []  # first look: we don't know what changed
        if phase != old:
            return self._phase(old, phase, old_score)
        if phase == "INGAME" and self.queue in ROUNDS_TO_WIN:
            return self._rounds(old_score, score)
        return []

    def summary(self) -> str:
        if self.phase == "INGAME":
            ally, enemy = self.score
            return f"Матч Valorant на карте {self.map}, режим {self.queue}, счёт {ally}:{enemy}."
        if self.phase == "PREGAME":
            return f"Пользователь выбирает агента перед матчем на карте {self.map}."
        return "Сейчас пользователь не в матче Valorant."

    def _phase(self, old: str, new: str, old_score: tuple[int, int]) -> list[Observation]:
        if new == "PREGAME":
            return [
                Observation(
                    f"Пользователь нашёл матч в Valorant на карте {self.map} и выбирает агента",
                    importance=0.4,
                    key="pregame",
                    quick=["Матч найден! Кого возьмёшь?"],
                )
            ]
        if new == "INGAME":
            self.streak.reset()
            return [
                Observation(
                    f"Начался матч в Valorant на карте {self.map}, режим {self.queue or 'custom'}",
                    importance=0.5,
                    key="match_start",
                    quick=["Погнали! Удачи!", "Поехали, я болею за тебя!"],
                    emotion="joy",
                )
            ]
        if new == "MENUS" and old == "INGAME":
            return self._match_result(old_score)
        return []

    def _match_result(self, score: tuple[int, int]) -> list[Observation]:
        ally, enemy = score
        if self.queue not in ROUNDS_TO_WIN or ally == enemy:
            return [
                Observation(
                    f"Матч в Valorant закончился ({self.queue or 'custom'})",
                    importance=0.5,
                    key="match_end",
                    quick=["Матч окончен. Как ощущения?"],
                )
            ]
        won = ally > enemy
        return [
            Observation(
                f"Команда пользователя {'выиграла' if won else 'проиграла'} матч в Valorant "
                f"на карте {self.map} со счётом {ally}:{enemy}",
                importance=1.0,
                key="match_end",
                quick=["Победа! Отличная игра!", "Мы выиграли! Это было круто!"]
                if won
                else ["Эх, не повезло. В следующий раз получится.", "Обидно. Реванш?"],
                emotion="joy" if won else "sad",
            )
        ]

    def _rounds(self, old: tuple[int, int], new: tuple[int, int]) -> list[Observation]:
        won = new[0] > old[0]
        lost = new[1] > old[1]
        if won == lost:  # no change, or a new match/reset of the score
            return []
        ally, enemy = new
        obs = self.streak.record(won=won, score=f"счёт {ally}:{enemy}")
        target = ROUNDS_TO_WIN[self.queue]
        if ally == target - 1 and enemy < target - 1:
            obs.text += ", у команды пользователя матч-поинт"
            obs.importance = max(obs.importance, 0.65)
        elif enemy == target - 1 and ally < target - 1:
            obs.text += ", у соперников матч-поинт"
            obs.importance = max(obs.importance, 0.6)
        return [obs]
