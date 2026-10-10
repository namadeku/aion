"""Valorant commentary from the Riot Client local API (the player's own presence)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from aion.sdk import Plugin, tool

from .tracker import ValorantTracker, decode_private

IDLE_POLL_S = 15.0  # how often to look for a running Riot Client


def lockfile_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Riot Games" / "Riot Client" / "Config" / "lockfile"


@dataclass(frozen=True)
class Lockfile:
    port: int
    password: str

    @classmethod
    def read(cls, path: Path) -> Lockfile | None:
        """``name:pid:port:password:protocol`` written by a running Riot Client."""
        try:
            parts = path.read_text(encoding="utf-8").strip().split(":")
            return cls(int(parts[2]), parts[3])
        except (OSError, IndexError, ValueError):
            return None


class Valorant(Plugin):
    async def on_load(self) -> None:
        self.tracker = ValorantTracker()
        self.create_task(self.watch(), name="valorant-presence")

    async def watch(self) -> None:
        while True:
            lock = Lockfile.read(lockfile_path())
            if lock is not None:
                try:
                    await self.follow(lock)
                except (httpx.HTTPError, KeyError, ValueError) as e:
                    self.log.debug("Клиент Riot недоступен: {}", e)
            await asyncio.sleep(IDLE_POLL_S)

    async def follow(self, lock: Lockfile) -> None:
        """Poll the player's presence while the Riot Client runs."""
        # The client serves HTTPS on localhost with its own self-signed certificate.
        async with httpx.AsyncClient(
            base_url=f"https://127.0.0.1:{lock.port}",
            auth=("riot", lock.password),
            verify=False,
            timeout=5,
        ) as client:
            session = (await client.get("/chat/v1/session")).raise_for_status().json()
            puuid = session["puuid"]
            self.log.info("Клиент Riot найден, слежу за матчами Valorant")
            while True:
                presence = await self.own_presence(client, puuid)
                if presence is not None:
                    for obs in self.tracker.update(presence):
                        self.react(obs)
                await asyncio.sleep(float(self.config["poll_seconds"]))

    @staticmethod
    async def own_presence(client: httpx.AsyncClient, puuid: str) -> dict[str, Any] | None:
        response = (await client.get("/chat/v4/presences")).raise_for_status()
        for item in response.json().get("presences", []):
            if item.get("puuid") == puuid and item.get("product") == "valorant":
                return decode_private(item.get("private") or "")
        return None

    @tool("Текущий матч пользователя в Valorant: карта, режим, счёт")
    async def match_status(self) -> str:
        return self.tracker.summary()
