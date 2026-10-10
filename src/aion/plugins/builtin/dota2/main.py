"""Dota 2 commentary via Game State Integration: the game POSTs its state to a local port."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import Any

from aion.sdk import Context, Plugin, command, serve_json, tool
from aion.sdk.games import find_steam_game, gsi_config, install_gsi_config

from .tracker import Dota2Tracker

GAME_FOLDER = "dota 2 beta"
CONFIG_PATH = ("game", "dota", "cfg", "gamestate_integration", "gamestate_integration_aion.cfg")
DATA = ["provider", "map", "player", "hero", "items"]


class Dota2(Plugin):
    server: asyncio.Task[Any] | None = None

    async def on_load(self) -> None:
        self.tracker = Dota2Tracker()
        self.token: str = await self.storage.get("token") or secrets.token_hex(8)
        await self.storage.set("token", self.token)
        await self.on_settings_changed()

    async def on_settings_changed(self) -> None:
        if self.config["install_config"]:
            await self.install()
        if self.server is not None:
            self.server.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.server  # free the port before binding it again
        self.server = self.create_task(
            serve_json(int(self.config["port"]), self.on_state), name="dota2-gsi"
        )

    async def install(self) -> str:
        game = find_steam_game(GAME_FOLDER)
        if game is None:
            return f"{self.g('Не нашёл', 'Не нашла')} Dota 2 в библиотеках Steam."
        text = gsi_config("Aion", int(self.config["port"]), self.token, DATA)
        if install_gsi_config(game.joinpath(*CONFIG_PATH), text):
            await self.notify(
                "Dota 2 подключена",
                "В Steam: Dota 2 → Свойства → Параметры запуска: -gamestateintegration. "
                "Если игра запущена, перезапустите её.",
            )
            return (
                f"{self.g('Подключил', 'Подключила')} Dota 2. Добавьте в Steam параметр "
                "запуска -gamestateintegration и перезапустите игру, если она запущена."
            )
        return "Dota 2 уже подключена."

    async def on_state(self, state: dict[str, Any]) -> None:
        if (state.get("auth") or {}).get("token") != self.token:
            return
        for obs in self.tracker.update(state):
            self.react(obs)

    @command(["(подключи|настрой) (доту|dota) [2]"])
    async def setup(self, ctx: Context) -> None:
        await ctx.say(await self.install())

    @tool("Текущая игра пользователя в Dota 2: герой, K/D/A, золото, время")
    async def match_status(self) -> str:
        return self.tracker.summary()
