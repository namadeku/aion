from __future__ import annotations

import asyncio
import base64
import json
import socket
from pathlib import Path
from typing import Any

import httpx

from aion.sdk.games import (
    RoundStreak,
    find_steam_game,
    gsi_config,
    install_gsi_config,
    steam_libraries,
)
from aion.sdk.webhook import serve_json
from tests.helpers import load_builtin_module

cs2 = load_builtin_module("cs2", "tracker")
dota = load_builtin_module("dota2", "tracker")
valorant = load_builtin_module("valorant", "tracker")


def texts(observations: list[Any]) -> list[str]:
    return [o.text for o in observations]


# -- shared helpers ---------------------------------------------------------------------


def test_round_streak() -> None:
    streak = RoundStreak()
    streak.record(won=False, score="0:0")
    streak.record(won=False, score="0:0")
    last = streak.record(won=False, score="0:0")
    assert "проиграла 3 раунда подряд" in last.text
    comeback = streak.record(won=True, score="1:3")
    assert "наконец выиграла раунд после 3 проигранных" in comeback.text
    assert comeback.importance > streak.record(won=True, score="2:3").importance
    streak.record(won=True, score="3:3")
    streak.record(won=True, score="4:3")
    last = streak.record(won=True, score="5:3")
    assert "выиграла 5 раундов подряд" in last.text


def test_gsi_config_install(tmp_path: Path) -> None:
    text = gsi_config("Aion", 3010, "tok", ["provider", "map"])
    assert '"uri"\t"http://127.0.0.1:3010/"' in text
    assert '"token"\t"tok"' in text
    assert '"map"\t"1"' in text
    path = tmp_path / "cfg" / "gamestate_integration_aion.cfg"
    assert install_gsi_config(path, text)
    assert not install_gsi_config(path, text)
    assert install_gsi_config(path, text.replace("3010", "3020"))


def test_steam_libraries(tmp_path: Path) -> None:
    root, other = tmp_path / "Steam", tmp_path / "Games"
    (root / "steamapps").mkdir(parents=True)
    (other / "steamapps" / "common" / "dota 2 beta").mkdir(parents=True)
    vdf = f'"libraryfolders"\n{{\n\t"1"\n\t{{\n\t\t"path"\t\t"{other}"\n\t}}\n}}'
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        vdf.replace("\\", "\\\\"), encoding="utf-8"
    )
    libraries = steam_libraries(root)
    assert libraries == [root, other]
    assert (
        find_steam_game("dota 2 beta", libraries) == other / "steamapps" / "common" / "dota 2 beta"
    )
    assert find_steam_game("Counter-Strike Global Offensive", libraries) is None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_serve_json_receives_posts() -> None:
    received: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        received.append(payload)

    port = free_port()
    server = asyncio.create_task(serve_json(port, handler))
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(50):  # wait until the server listens
                try:
                    await client.post("/", content=b"not json")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.02)
            response = await client.post("/", json={"map": {"phase": "live"}})
            assert response.status_code == 200
            assert (await client.post("/", content=b"not json")).status_code == 400
    finally:
        server.cancel()
    assert received == [{"map": {"phase": "live"}}]


# -- CS2 ----------------------------------------------------------------------------------


def cs_state(
    *,
    phase: str = "live",
    kills: int = 0,
    deaths: int = 0,
    round_kills: int = 0,
    health: int = 100,
    round_phase: str = "live",
    win_team: str | None = None,
    ct: int = 0,
    t: int = 0,
    spectating: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "provider": {"steamid": "me"},
        "map": {"phase": phase, "name": "de_mirage", "mode": "competitive",
                "team_ct": {"score": ct}, "team_t": {"score": t}},
        "round": {"phase": round_phase},
        "player": {
            "steamid": "mate" if spectating else "me",
            "team": "CT",
            "state": {"health": health, "round_kills": round_kills, "round_killhs": 0},
            "match_stats": {"kills": kills, "deaths": deaths, "assists": 0, "mvps": 0},
        },
    }  # fmt: skip
    if win_team:
        state["round"]["win_team"] = win_team
    return state


def test_cs2_match_start_and_multikill() -> None:
    tracker = cs2.Cs2Tracker()
    assert tracker.update(cs_state(phase="warmup")) == []
    assert texts(tracker.update(cs_state())) == ["Пользователь начал матч в CS2 на карте de_mirage"]
    tracker.update(cs_state(kills=1, round_kills=1))
    (obs,) = tracker.update(cs_state(kills=3, round_kills=3))
    assert "тройное убийство" in obs.text
    assert obs.importance >= 0.75
    (ace,) = tracker.update(cs_state(kills=5, round_kills=5))
    assert ace.importance == 1.0


def test_cs2_ignores_spectated_teammate() -> None:
    tracker = cs2.Cs2Tracker()
    tracker.update(cs_state())
    assert texts(tracker.update(cs_state(deaths=1, health=0))) == [
        "Пользователя убили, он не успел никого убить в этом раунде"
    ]
    # while dead GSI shows a teammate: their kills are not ours
    assert tracker.update(cs_state(kills=4, round_kills=2, spectating=True)) == []


def test_cs2_round_and_match_result() -> None:
    tracker = cs2.Cs2Tracker()
    tracker.update(cs_state(ct=11, t=5))
    (obs,) = tracker.update(cs_state(round_phase="over", win_team="CT", ct=12, t=5))
    assert (
        obs.text
        == "Команда пользователя выиграла раунд, счёт 12:5, у команды пользователя матч-поинт"
    )
    (end,) = tracker.update(cs_state(phase="gameover", ct=13, t=5))
    assert end.text == "Команда пользователя выиграла матч в CS2 со счётом 13:5"
    assert end.importance == 1.0
    assert "счёт 13:5" in tracker.summary()


def test_cs2_low_health_once() -> None:
    tracker = cs2.Cs2Tracker()
    tracker.update(cs_state(health=80))
    assert texts(tracker.update(cs_state(health=12))) == [
        "У пользователя осталось всего 12 здоровья"
    ]
    assert tracker.update(cs_state(health=8)) == []


# -- Dota 2 -------------------------------------------------------------------------------


def dota_state(
    *,
    game_state: str = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
    kills: int = 0,
    deaths: int = 0,
    streak: int = 0,
    clock: int = 600,
    level: int = 5,
    items: list[str] | None = None,
    win_team: str = "none",
) -> dict[str, Any]:
    return {
        "map": {"game_state": game_state, "clock_time": clock, "win_team": win_team},
        "player": {"kills": kills, "deaths": deaths, "assists": 0, "kill_streak": streak,
                   "gold": 600, "team_name": "radiant", "last_hits": 50, "gpm": 400},
        "hero": {"name": "npc_dota_hero_nevermore", "level": level, "alive": True,
                 "health_percent": 100},
        "items": {f"slot{i}": {"name": name} for i, name in enumerate(items or [])},
    }  # fmt: skip


def test_dota_hero_names() -> None:
    assert dota.hero_name("npc_dota_hero_nevermore") == "Shadow Fiend"
    assert dota.hero_name("npc_dota_hero_crystal_maiden") == "Crystal Maiden"


def test_dota_game_flow() -> None:
    tracker = dota.Dota2Tracker()
    tracker.update(dota_state(game_state="DOTA_GAMERULES_STATE_PRE_GAME"))
    assert texts(tracker.update(dota_state())) == ["Началась игра в Dota 2: крипы вышли на линии"]
    assert texts(tracker.update(dota_state(level=6))) == [
        "Герой пользователя достиг 6 уровня — ультимейт"
    ]
    (spree,) = tracker.update(dota_state(level=6, kills=3, streak=3))
    assert "Killing Spree" in spree.text
    (aegis,) = tracker.update(dota_state(level=6, kills=3, streak=3, items=["item_aegis"]))
    assert "Aegis" in aegis.text
    (end,) = tracker.update(
        dota_state(game_state="DOTA_GAMERULES_STATE_POST_GAME", win_team="radiant", level=6)
    )
    assert end.text == "Команда пользователя выиграла игру в Dota 2"
    assert "Shadow Fiend" in tracker.summary()


def test_dota_feeding() -> None:
    tracker = dota.Dota2Tracker()
    tracker.update(dota_state())
    tracker.update(dota_state(deaths=1, clock=600))
    tracker.update(dota_state(deaths=2, clock=700))
    (obs,) = tracker.update(dota_state(deaths=3, clock=800))
    assert obs.text == "Героя пользователя убили уже 3 раза за последние 5 минут"


def test_dota_ignores_spectating() -> None:
    tracker = dota.Dota2Tracker()
    spectate = {"map": {"game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"},
                "player": {"team2": {"player0": {"kills": 1}}}}  # fmt: skip
    tracker.update(spectate)
    assert tracker.update(spectate) == []


# -- Valorant -----------------------------------------------------------------------------


def presence(loop: str, ally: int = 0, enemy: int = 0, *, nested: bool = False) -> dict[str, Any]:
    match = {"sessionLoopState": loop, "matchMap": "/Game/Maps/Bonsai/Bonsai",
             "queueId": "competitive"}  # fmt: skip
    party = {"partyOwnerMatchScoreAllyTeam": ally, "partyOwnerMatchScoreEnemyTeam": enemy}
    if nested:
        return {"matchPresenceData": match, "partyPresenceData": party}
    return match | party


def test_valorant_decode_private() -> None:
    raw = base64.b64encode(json.dumps(presence("MENUS")).encode()).decode()
    assert valorant.decode_private(raw)["sessionLoopState"] == "MENUS"
    assert valorant.decode_private("garbage!") == {}


def test_valorant_match_flow() -> None:
    tracker = valorant.ValorantTracker()
    assert tracker.update(presence("MENUS")) == []
    (pregame,) = tracker.update(presence("PREGAME"))
    assert "на карте Split" in pregame.text
    (start,) = tracker.update(presence("INGAME", nested=True))
    assert start.text == "Начался матч в Valorant на карте Split, режим competitive"
    (won,) = tracker.update(presence("INGAME", 1, 0, nested=True))
    assert won.text == "Команда пользователя выиграла раунд, счёт 1:0"
    assert tracker.update(presence("INGAME", 1, 0)) == []
    (point,) = tracker.update(presence("INGAME", 1, 12))
    assert "у соперников матч-поинт" in point.text
    (end,) = tracker.update(presence("MENUS"))
    assert (
        end.text == "Команда пользователя проиграла матч в Valorant на карте Split со счётом 1:12"
    )
