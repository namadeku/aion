"""Turns successive CS2 Game State Integration snapshots into observations worth a remark."""

from __future__ import annotations

from typing import Any

from aion.core.initiative import Observation
from aion.sdk.games import RoundStreak

State = dict[str, Any]

MULTI_KILLS = {
    2: ("двойное убийство", 0.4, ["Дабл!", "Двоих сразу!"]),
    3: ("тройное убийство", 0.75, ["Трипл! Вот это да!", "Троих! Отлично!"]),
    4: ("четыре убийства", 0.9, ["Квадро! Ещё одного!", "Четверо! Невероятно!"]),
    5: ("эйс — убил всю команду противника", 1.0, ["Эйс! Это было великолепно!", "Эйс!!!"]),
}


def _get(state: State, *path: str) -> Any:
    value: Any = state
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)  # pyright: ignore[reportUnknownMemberType]
    return value


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


class Cs2Tracker:
    def __init__(self) -> None:
        self.prev: State = {}
        self.team: str | None = None  # "CT" / "T" of the local player
        self.streak = RoundStreak()

    def own(self, state: State) -> State | None:
        """The local player's block; while dead GSI shows the spectated teammate instead."""
        player = state.get("player")
        if not isinstance(player, dict):
            return None
        steamid = _get(state, "provider", "steamid")
        return player if steamid and player.get("steamid") == steamid else None  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]

    def update(self, state: State) -> list[Observation]:
        prev, self.prev = self.prev, state
        me = self.own(state)
        if me is not None and me.get("team") in ("CT", "T"):
            self.team = me["team"]
        if not prev:
            return []
        out: list[Observation] = []
        out += self._match(prev, state)
        out += self._player(self.own(prev), me)
        out += self._round(prev, state)
        return out

    def summary(self) -> str:
        """Current match for the LLM tool: map, score, the player's stats."""
        state = self.prev
        if not isinstance(state.get("map"), dict):
            return "Сейчас пользователь не в матче CS2."
        ct, t = (
            _int(_get(state, "map", "team_ct", "score")),
            _int(_get(state, "map", "team_t", "score")),
        )
        mine, theirs = (ct, t) if self.team == "CT" else (t, ct)
        parts = [
            f"Карта {_get(state, 'map', 'name')}, режим {_get(state, 'map', 'mode')}",
            f"счёт {mine}:{theirs} (команда пользователя {self.team or '?'})",
        ]
        me = self.own(state)
        if me is not None:
            stats = me.get("match_stats") or {}
            parts.append(
                f"убийств {stats.get('kills', 0)}, смертей {stats.get('deaths', 0)}, "
                f"помощи {stats.get('assists', 0)}, MVP {stats.get('mvps', 0)}"
            )
            hp = _get(me, "state", "health")
            if hp is not None:
                parts.append(f"здоровье {hp}, деньги {_get(me, 'state', 'money')}")
        return "; ".join(parts) + "."

    # -- match ----------------------------------------------------------------------------

    def _match(self, prev: State, cur: State) -> list[Observation]:
        old, new = _get(prev, "map", "phase"), _get(cur, "map", "phase")
        if new == old:
            return []
        if new == "live" and old in (None, "warmup"):
            self.streak.reset()
            name = _get(cur, "map", "name") or "неизвестной карте"
            return [
                Observation(
                    f"Пользователь начал матч в CS2 на карте {name}",
                    importance=0.5,
                    key="match_start",
                    quick=["Погнали! Удачи в матче!", "Поехали, я болею за тебя!"],
                    emotion="joy",
                )
            ]
        if new == "gameover":
            return [self._match_result(cur)]
        return []

    def _match_result(self, state: State) -> Observation:
        ct = _int(_get(state, "map", "team_ct", "score"))
        t = _int(_get(state, "map", "team_t", "score"))
        mine, theirs = (ct, t) if self.team == "CT" else (t, ct)
        if mine > theirs:
            return Observation(
                f"Команда пользователя выиграла матч в CS2 со счётом {mine}:{theirs}",
                importance=1.0,
                key="match_end",
                quick=["Победа! Отличная игра!", "Мы выиграли! Это было круто!"],
                emotion="joy",
            )
        if mine < theirs:
            return Observation(
                f"Команда пользователя проиграла матч в CS2 со счётом {mine}:{theirs}",
                importance=1.0,
                key="match_end",
                quick=["Эх, не повезло. В следующий раз точно получится.", "Обидно. Реванш?"],
                emotion="sad",
            )
        return Observation(
            f"Матч в CS2 закончился ничьей {mine}:{theirs}",
            importance=0.9,
            key="match_end",
            quick=["Ничья! Напряжённо было."],
        )

    # -- player ---------------------------------------------------------------------------

    def _player(self, prev: State | None, cur: State | None) -> list[Observation]:
        if prev is None or cur is None:
            return []
        out: list[Observation] = []
        kills = _int(_get(cur, "match_stats", "kills")) - _int(_get(prev, "match_stats", "kills"))
        round_kills = _int(_get(cur, "state", "round_kills"))
        if kills > 0 and round_kills >= 2:
            what, importance, quick = MULTI_KILLS[min(round_kills, 5)]
            out.append(
                Observation(
                    f"Пользователь сделал {what} в раунде",
                    importance=importance,
                    key=f"multikill{round_kills}",
                    quick=quick,
                    emotion="joy" if round_kills < 5 else "surprise",
                )
            )
        elif kills > 0:
            headshot = _int(_get(cur, "state", "round_killhs")) > _int(
                _get(prev, "state", "round_killhs")
            )
            out.append(
                Observation(
                    "Пользователь убил противника" + (" выстрелом в голову" if headshot else ""),
                    importance=0.3 if headshot else 0.2,
                    key="kill",
                    quick=["В голову!", "Чисто!"] if headshot else ["Есть!", "Минус один!"],
                    emotion="joy",
                    ttl=5,
                )
            )
        deaths = _int(_get(cur, "match_stats", "deaths")) - _int(
            _get(prev, "match_stats", "deaths")
        )
        if deaths > 0:
            empty = ", он не успел никого убить в этом раунде" if round_kills == 0 else ""
            out.append(
                Observation(
                    f"Пользователя убили{empty}",
                    importance=0.45,
                    key="death",
                    quick=["Ой! Обидно.", "Эх, не повезло.", "Ничего, в следующем раунде."],
                    emotion="sad",
                    ttl=6,
                )
            )
        mvps = _int(_get(cur, "match_stats", "mvps")) - _int(_get(prev, "match_stats", "mvps"))
        if mvps > 0:
            out.append(
                Observation(
                    "Пользователь стал MVP раунда",
                    importance=0.6,
                    key="mvp",
                    quick=["MVP раунда! Так держать!"],
                    emotion="joy",
                )
            )
        health, before = _get(cur, "state", "health"), _get(prev, "state", "health")
        if isinstance(health, int) and isinstance(before, int) and 0 < health <= 20 < before:
            out.append(
                Observation(
                    f"У пользователя осталось всего {health} здоровья",
                    importance=0.3,
                    key="low_hp",
                    cooldown=30,
                    quick=["Осторожно, мало здоровья!", "Береги себя, здоровья почти нет!"],
                    emotion="surprise",
                    ttl=4,
                )
            )
        return out

    # -- round ------------------------------------------------------------------------------

    def _round(self, prev: State, cur: State) -> list[Observation]:
        out: list[Observation] = []
        bomb, old_bomb = _get(cur, "round", "bomb"), _get(prev, "round", "bomb")
        if bomb != old_bomb and bomb in ("planted", "defused", "exploded"):
            out.append(self._bomb(bomb))
        ended = _get(cur, "round", "phase") == "over" != _get(prev, "round", "phase")
        winner = _get(cur, "round", "win_team")
        if ended and winner in ("CT", "T") and self.team and _get(cur, "map", "phase") == "live":
            out.append(self._round_result(cur, won=winner == self.team))
        return out

    def _bomb(self, bomb: str) -> Observation:
        attacking = self.team == "T"
        match bomb:
            case "planted":
                text = "Бомба заложена" + (
                    " командой пользователя" if attacking else ", надо дефузить"
                )
                return Observation(
                    text, importance=0.3, key="bomb", ttl=5, quick=["Бомба на точке!"]
                )
            case "defused":
                good = not attacking
                return Observation(
                    "Бомбу обезвредили" + (" — раунд наш" if good else ", раунд потерян"),
                    importance=0.5,
                    key="bomb",
                    ttl=6,
                    quick=["Дефуз! Красиво!"] if good else ["Эх, раздефузили."],
                    emotion="joy" if good else "sad",
                )
            case _:
                return Observation(
                    "Бомба взорвалась" + (" — раунд наш" if attacking else ", раунд потерян"),
                    importance=0.4,
                    key="bomb",
                    ttl=6,
                    quick=["Бум! Раунд наш!"] if attacking else ["Не успели..."],
                    emotion="joy" if attacking else "sad",
                )

    def _round_result(self, state: State, *, won: bool) -> Observation:
        ct = _int(_get(state, "map", "team_ct", "score"))
        t = _int(_get(state, "map", "team_t", "score"))
        mine, theirs = (ct, t) if self.team == "CT" else (t, ct)
        obs = self.streak.record(won=won, score=f"счёт {mine}:{theirs}")
        if mine == 12 and theirs < 12 and _get(state, "map", "mode") in ("competitive", "premier"):
            obs.text += ", у команды пользователя матч-поинт"
            obs.importance = max(obs.importance, 0.65)
        return obs
