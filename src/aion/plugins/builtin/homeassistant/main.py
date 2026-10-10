"""Home Assistant integration via its REST API — also a template for your own integrations."""

from __future__ import annotations

import time
from typing import Annotated, Any

import httpx
from rapidfuzz import fuzz, process

from aion.sdk import Context, Plugin, command, tool

CACHE_SECONDS = 60


class HomeAssistant(Plugin):
    async def on_load(self) -> None:
        self._states: list[dict[str, Any]] = []
        self._fetched = 0.0

    async def on_settings_changed(self) -> None:
        self._fetched = 0.0  # re-read devices with the new address/token

    # -- REST helpers ---------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.config["token"]) and bool(self.config["url"])

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        url = str(self.config["url"]).rstrip("/") + path
        headers = {"Authorization": f"Bearer {self.config['token']}"}
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.request(method, url, headers=headers, json=json)
            response.raise_for_status()
            return response.json()

    async def states(self) -> list[dict[str, Any]]:
        if time.time() - self._fetched > CACHE_SECONDS:
            self._states = await self._request("GET", "/api/states")
            self._fetched = time.time()
        return self._states

    async def find(self, spoken: str, domains: set[str] | None = None) -> dict[str, Any] | None:
        """Fuzzy-match a spoken name against entity friendly names."""
        candidates = {
            s["attributes"].get("friendly_name", s["entity_id"]).lower(): s
            for s in await self.states()
            if domains is None or s["entity_id"].split(".")[0] in domains
        }
        hit = process.extractOne(
            spoken.lower(), list(candidates), scorer=fuzz.WRatio, score_cutoff=75
        )
        return candidates[hit[0]] if hit else None

    def _domains(self) -> set[str]:
        return {d.strip() for d in str(self.config["domains"]).split(",") if d.strip()}

    async def _switch(self, ctx: Context, device: str, turn_on: bool) -> bool:
        if not self.configured:
            return False  # not set up: let other plugins handle "включи …"
        try:
            entity = await self.find(device, self._domains())
        except httpx.HTTPError as e:
            self.log.warning("Home Assistant недоступен: {}", e)
            return False
        if entity is None:
            return False
        entity_id = entity["entity_id"]
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"
        if domain == "cover":
            service = "open_cover" if turn_on else "close_cover"
        await self._request("POST", f"/api/services/{domain}/{service}", {"entity_id": entity_id})
        self._fetched = 0.0
        name = entity["attributes"].get("friendly_name", entity_id)
        await ctx.say(f"{'Включаю' if turn_on else 'Выключаю'}: {name}.")
        return True

    # -- commands (priority 1: checked before "включи <программа>", declined if no device) --

    @command(["(включи|зажги|открой) {device}"], priority=1)
    async def turn_on(self, ctx: Context, device: str) -> bool:
        return await self._switch(ctx, device, True)

    @command(["(выключи|погаси|закрой) {device}"], priority=1)
    async def turn_off(self, ctx: Context, device: str) -> bool:
        return await self._switch(ctx, device, False)

    @command(["какая температура (в|на) {room}", "сколько градусов (в|на) {room}"])
    async def temperature(self, ctx: Context, room: str) -> bool:
        if not self.configured:
            return False
        sensors = [
            s for s in await self.states() if s["attributes"].get("device_class") == "temperature"
        ]
        names = {s["attributes"].get("friendly_name", s["entity_id"]).lower(): s for s in sensors}
        hit = process.extractOne(room.lower(), list(names), scorer=fuzz.WRatio, score_cutoff=60)
        if not hit:
            return False
        sensor = names[hit[0]]
        unit = sensor["attributes"].get("unit_of_measurement", "°")
        await ctx.say(f"{sensor['attributes'].get('friendly_name')}: {sensor['state']}{unit}.")
        return True

    # -- LLM tools ------------------------------------------------------------------------

    @tool("Состояние устройства или датчика умного дома по названию")
    async def ha_state(self, name: Annotated[str, "Название устройства/датчика"]) -> str:
        entity = await self.find(name)
        if entity is None:
            return "Не найдено"
        unit = entity["attributes"].get("unit_of_measurement", "")
        return f"{entity['entity_id']}: {entity['state']}{unit}"

    @tool("Вызвать сервис Home Assistant (например light.turn_on)")
    async def ha_call_service(
        self,
        domain: Annotated[str, "light, switch, climate…"],
        service: Annotated[str, "turn_on, turn_off, set_temperature…"],
        entity_id: str,
        data: dict[str, Any] | None = None,
    ) -> str:
        await self._request(
            "POST", f"/api/services/{domain}/{service}", {"entity_id": entity_id, **(data or {})}
        )
        self._fetched = 0.0
        return "Выполнено"
