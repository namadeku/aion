"""Turns successive Dota 2 Game State Integration snapshots into observations worth a remark."""

from __future__ import annotations

from collections import deque
from typing import Any

from aion.core.initiative import Observation
from aion.text.plural import fmt_count

State = dict[str, Any]

# internal hero names that differ from the in-game ones
HERO_NAMES = {
    "abyssal_underlord": "Underlord",
    "antimage": "Anti-Mage",
    "centaur": "Centaur Warrunner",
    "doom_bringer": "Doom",
    "furion": "Nature's Prophet",
    "life_stealer": "Lifestealer",
    "magnataur": "Magnus",
    "necrolyte": "Necrophos",
    "nevermore": "Shadow Fiend",
    "obsidian_destroyer": "Outworld Destroyer",
    "queenofpain": "Queen of Pain",
    "rattletrap": "Clockwerk",
    "shredder": "Timbersaw",
    "skeleton_king": "Wraith King",
    "treant": "Treant Protector",
    "vengefulspirit": "Vengeful Spirit",
    "windrunner": "Windranger",
    "wisp": "Io",
    "zuus": "Zeus",
}

STREAKS = {
    3: ("Killing Spree", 0.55),
    4: ("Dominating", 0.6),
    5: ("Mega Kill", 0.7),
    6: ("Unstoppable", 0.75),
    7: ("Wicked Sick", 0.8),
    8: ("Monster Kill", 0.85),
    9: ("Godlike", 0.95),
    10: ("Beyond Godlike", 1.0),
}

IN_PROGRESS = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"
POST_GAME = "DOTA_GAMERULES_STATE_POST_GAME"
FEED_WINDOW_S = 300  # deaths within this much game time count as a bad streak


def hero_name(internal: str) -> str:
    """``npc_dota_hero_nevermore`` -> ``Shadow Fiend``."""
    short = internal.removeprefix("npc_dota_hero_")
    return HERO_NAMES.get(short) or short.replace("_", " ").title()


def _dict(value: Any) -> State:
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


class Dota2Tracker:
    def __init__(self) -> None:
        self.prev: State = {}
        self.deaths_at: deque[int] = deque(maxlen=10)  # game clock of recent deaths

    def update(self, state: State) -> list[Observation]:
        prev, self.prev = self.prev, state
        if not prev:
            return []
        player, old_player = _dict(state.get("player")), _dict(prev.get("player"))
        if "kills" not in player:  # spectating: "player" lists everyone, not us
            return []
        out = self._game(prev, state)
        out += self._hero(_dict(prev.get("hero")), _dict(state.get("hero")))
        if "kills" in old_player:
            clock = _int(_dict(state.get("map")).get("clock_time"))
            out += self._player(old_player, player, clock)
            out += self._items(_dict(prev.get("items")), _dict(state.get("items")))
        return out

    def summary(self) -> str:
        """Current game for the LLM tool: hero, K/D/A, net worth, clock."""
        state = self.prev
        player, hero, game = (_dict(state.get(k)) for k in ("player", "hero", "map"))
        if "kills" not in player:
            return "Сейчас пользователь не в игре Dota 2."
        clock = _int(game.get("clock_time"))
        parts = [
            f"Герой {hero_name(str(hero.get('name', '')))} {hero.get('level', '?')} уровня",
            f"K/D/A {player.get('kills')}/{player.get('deaths')}/{player.get('assists')}",
            f"добито крипов {player.get('last_hits')}, золото {player.get('gold')}, "
            f"GPM {player.get('gpm')}",
            f"время игры {clock // 60}:{abs(clock) % 60:02d}, "
            f"команда {player.get('team_name', '?')}",
        ]
        return "; ".join(parts) + "."

    # -- game -----------------------------------------------------------------------------

    def _game(self, prev: State, cur: State) -> list[Observation]:
        old = _dict(prev.get("map")).get("game_state")
        new = _dict(cur.get("map")).get("game_state")
        if new == old:
            return []
        if new == IN_PROGRESS:
            self.deaths_at.clear()
            return [
                Observation(
                    "Началась игра в Dota 2: крипы вышли на линии",
                    importance=0.5,
                    key="game_start",
                    quick=["Погнали! Удачной игры!", "Крипы пошли, удачи!"],
                    emotion="joy",
                )
            ]
        if new == POST_GAME:
            winner = _dict(cur.get("map")).get("win_team")
            team = _dict(cur.get("player")).get("team_name")
            if winner not in ("radiant", "dire") or not team:
                return []
            won = winner == team
            return [
                Observation(
                    f"Команда пользователя {'выиграла' if won else 'проиграла'} игру в Dota 2",
                    importance=1.0,
                    key="game_end",
                    quick=["Победа! Отличная игра!", "Трон наш!"]
                    if won
                    else ["Эх, не вышло. Следующую возьмём.", "Обидно. Реванш?"],
                    emotion="joy" if won else "sad",
                )
            ]
        return []

    def _hero(self, prev: State, cur: State) -> list[Observation]:
        out: list[Observation] = []
        name = cur.get("name")
        if isinstance(name, str) and name.startswith("npc_dota_hero_") and name != prev.get("name"):
            out.append(
                Observation(
                    f"Пользователь играет за героя {hero_name(name)}",
                    importance=0.45,
                    key="hero_pick",
                    quick=["Хороший выбор героя!", "О, интересный пик!"],
                )
            )
        level, before = _int(cur.get("level")), _int(prev.get("level"))
        if before and level > before and level in (6, 30):
            what = "ультимейт" if level == 6 else "максимальный, 30-й уровень"
            out.append(
                Observation(
                    f"Герой пользователя достиг {level} уровня — {what}",
                    importance=0.35 if level == 6 else 0.6,
                    key=f"level{level}",
                    quick=["Шестой уровень, ульта готова!"] if level == 6 else ["Тридцатый!"],
                    emotion="joy",
                )
            )
        hp, hp_before = cur.get("health_percent"), prev.get("health_percent")
        alive = cur.get("alive") is True
        if (
            alive
            and isinstance(hp, int)
            and isinstance(hp_before, int)
            and 0 < hp <= 15 < hp_before
        ):
            out.append(
                Observation(
                    f"У героя пользователя осталось {hp}% здоровья",
                    importance=0.3,
                    key="low_hp",
                    cooldown=30,
                    ttl=4,
                    quick=["Осторожно, мало здоровья!", "Отходи, ты почти без здоровья!"],
                    emotion="surprise",
                )
            )
        return out

    # -- player -----------------------------------------------------------------------------

    def _player(self, prev: State, cur: State, clock: int) -> list[Observation]:
        out: list[Observation] = []
        kills = _int(cur.get("kills")) - _int(prev.get("kills"))
        streak = _int(cur.get("kill_streak"))
        if kills > 0 and streak >= 3:
            title, importance = STREAKS[min(streak, 10)]
            out.append(
                Observation(
                    f"Пользователь убил героя и у него серия {streak} убийств без смертей "
                    f"({title})",
                    importance=importance,
                    key=f"streak{min(streak, 10)}",
                    quick=[f"{title}!", f"{title}! Тебя не остановить!"],
                    emotion="joy" if streak < 9 else "surprise",
                )
            )
        elif kills > 0:
            out.append(
                Observation(
                    "Пользователь убил вражеского героя",
                    importance=0.3,
                    key="kill",
                    ttl=6,
                    quick=["Есть!", "Отличный килл!"],
                    emotion="joy",
                )
            )
        if _int(cur.get("deaths")) > _int(prev.get("deaths")):
            out.append(self._death(clock))
        gold, gold_before = _int(cur.get("gold")), _int(prev.get("gold"))
        if gold >= 5000 > gold_before:
            out.append(
                Observation(
                    f"У пользователя накопилось {gold} золота, а он ничего не покупает",
                    importance=0.3,
                    key="gold",
                    cooldown=300,
                    quick=["Столько золота! Может, закупишься?"],
                    emotion="thinking",
                )
            )
        return out

    def _death(self, clock: int) -> Observation:
        self.deaths_at.append(clock)
        recent = sum(1 for t in self.deaths_at if clock - t <= FEED_WINDOW_S)
        if recent >= 3:
            return Observation(
                f"Героя пользователя убили уже {fmt_count(recent, ('раз', 'раза', 'раз'))} "
                "за последние 5 минут",
                importance=0.65,
                key="feeding",
                quick=["Может, стоит поиграть осторожнее?", "Опять? Давай аккуратнее!"],
                emotion="sad",
            )
        return Observation(
            "Героя пользователя убили",
            importance=0.45,
            key="death",
            ttl=6,
            quick=["Ой! Обидно.", "Эх, не повезло.", "Ничего, отомстим."],
            emotion="sad",
        )

    def _items(self, prev: State, cur: State) -> list[Observation]:
        def names(items: State) -> set[str]:
            return {str(_dict(slot).get("name")) for slot in items.values()}

        if "item_aegis" in names(cur) - names(prev):
            return [
                Observation(
                    "Пользователь подобрал Aegis после убийства Рошана",
                    importance=0.7,
                    key="aegis",
                    quick=["Аегис наш!", "Рошан пал, аегис у тебя!"],
                    emotion="joy",
                )
            ]
        return []
