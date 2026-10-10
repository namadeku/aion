"""Volume, music players, lock screen, screenshots and power control."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from typing import Annotated, Any, Literal

from aion.sdk import Context, Plugin, command, tool

from . import control, media
from .control import PowerAction

APP_START_S = 15.0  # how long to wait for a just-opened player to show up
# players named right in the command: "включи яндекс музыку"
_APP_IN_PHRASE = ("яндекс", "спотифай", "spotify", "вк музыку")

_POWER_DONE = {
    "shutdown": "Выключаю компьютер через 10 секунд. "
    "Скажите «отмени выключение», если передумаете.",
    "restart": "Перезагружаю через 10 секунд.",
    "sleep": "Перевожу в спящий режим.",
    "cancel": "Выключение отменено.",
}

_FAILURES = (control.Unsupported, subprocess.SubprocessError, OSError)


class System(Plugin):
    async def _do(self, ctx: Context, func: Callable[..., Any], *args: Any, ok: str = "") -> bool:
        try:
            await asyncio.to_thread(func, *args)
        except _FAILURES as e:
            await ctx.say(f"Не получилось: {e}.")
            return False
        if ok:
            await ctx.say(ok)
        return True

    # -- volume ---------------------------------------------------------------------------

    async def _volume(self, ctx: Context, func: Callable[..., int | None], *args: Any) -> None:
        """Change the volume and say the new level (where the system reports it)."""
        try:
            level = await asyncio.to_thread(func, *args)
        except _FAILURES as e:
            await ctx.say(f"Не получилось: {e}.")
            return
        if level is not None:
            await ctx.say(f"{level}%.")

    @command(["[сделай] (громче|погромче)", "(прибавь|увеличь) [громкость|звук]"])
    async def louder(self, ctx: Context) -> None:
        await self._volume(ctx, control.change_volume, int(self.config["volume_step"]), True)

    @command(["[сделай] (тише|потише)", "(убавь|уменьши) [громкость|звук]"])
    async def quieter(self, ctx: Context) -> None:
        await self._volume(ctx, control.change_volume, int(self.config["volume_step"]), False)

    @command(
        [
            "[сделай] (громче|погромче) на {amount} [процентов|процента]",
            "(прибавь|увеличь) [громкость|звук] на {amount} [процентов|процента]",
        ]
    )
    async def louder_by(self, ctx: Context, amount: int) -> None:
        await self._volume(ctx, control.change_volume, amount, True)

    @command(
        [
            "[сделай] (тише|потише) на {amount} [процентов|процента]",
            "(убавь|уменьши) [громкость|звук] на {amount} [процентов|процента]",
        ]
    )
    async def quieter_by(self, ctx: Context, amount: int) -> None:
        await self._volume(ctx, control.change_volume, amount, False)

    @command(
        [
            "[поставь|сделай|поменяй|установи|выставь] громкость [на] {level} [процентов|процента]",
            "(убавь|прибавь|сделай|поставь|уменьши|увеличь) (звук|громкость) до {level} "
            "[процентов|процента]",
        ]
    )
    async def volume(self, ctx: Context, level: int) -> None:
        await self._volume(ctx, control.set_volume, level)

    @command(
        [
            "[сделай|поставь|выкрути] (звук|громкость) на (минимум|максимум|полную)",
            "(выкрути|сделай) на (минимум|максимум|полную)",
        ]
    )
    async def volume_extreme(self, ctx: Context) -> None:
        level = 0 if "миним" in ctx.text.lower() else 100
        await self._volume(ctx, control.set_volume, level)

    @command(["какая [сейчас] громкость", "сколько [сейчас] громкость"])
    async def current_volume(self, ctx: Context) -> None:
        level = await asyncio.to_thread(control.get_volume)
        await ctx.say(f"Громкость {level}%." if level is not None else "Не могу узнать громкость.")

    @command(["(выключи|отключи) звук", "без звука"])
    async def mute(self, ctx: Context) -> None:
        await self._do(ctx, control.set_mute, True)

    @command(["включи звук", "верни звук"])
    async def unmute(self, ctx: Context) -> None:
        await self._do(ctx, control.set_mute, False)

    # -- music ------------------------------------------------------------------------------

    async def play_music(self, app: str = "") -> str:
        """Resume the named or the active player, opening the app if needed. Returns a reply."""
        try:
            player = await media.play(app or None)
        except media.MediaUnavailable:
            await asyncio.to_thread(control.media, "play_pause")  # best effort: the media key
            return ""
        if player is not None:
            return _started(player)
        target = app or str(self.config["music_app"]).strip()
        if not target:
            return (
                "Не вижу открытого плеера. Скажите, например, «включи музыку в Яндекс Музыке» "
                "или выберите плеер по умолчанию в настройках."
            )
        title = await self._open_app(target)
        if title is None:
            # a fuzzy match may take "хочу расслабиться" for an app name: any open player will
            # do — but not when a real player was named ("спотифай" is not "яндекс")
            if app and not media.is_player_name(app) and (player := await media.play(None)):
                return _started(player)
            return f"{self.g('Не нашёл', 'Не нашла')} приложение «{target}»."
        for _ in range(int(APP_START_S / 0.5)):  # the app takes a moment to show up
            await asyncio.sleep(0.5)
            if (player := await media.play(target)) is not None:
                return _started(player)
        opened = self.g("Открыл", "Открыла")
        return f"{opened} {title}, но запустить музыку не вышло — нажмите плей."

    async def _open_app(self, name: str) -> str | None:
        """Open an app with the apps plugin; returns how to call it, or None if not found."""
        tools = {t.qualified_name: t for t in self.host.plugins.tools()}
        spec = tools.get("apps__open_app")
        if spec is None:
            return None
        result = await spec.invoke({"name": name})
        return None if result.startswith("Не найдено") else name

    @command(
        [
            "(включи|поставь|запусти) музыку",
            "(продолжи|возобнови) (музыку|воспроизведение)",
            "(включи|поставь|запусти) музыку (в|на|из) {app}",
            "(включи|запусти) (яндекс музыку|спотифай|spotify|вк музыку)",
        ],
        priority=2,
    )
    async def play(self, ctx: Context, app: str = "") -> None:
        named = next((a for a in _APP_IN_PHRASE if a in ctx.text.lower()), "")
        if phrase := await self.play_music(app or named):
            await ctx.say(phrase)

    @command(
        [
            "пауза",
            "(поставь|поставить) на паузу",
            "(останови|выключи|приостанови) (музыку|видео|воспроизведение|песню)",
        ]
    )
    async def pause(self, ctx: Context) -> None:
        try:
            if not await media.pause():
                await ctx.say("Сейчас ничего не играет.")
        except media.MediaUnavailable:
            await self._do(ctx, control.media, "play_pause")

    async def _skip(self, ctx: Context, forward: bool) -> None:
        try:
            if await media.skip(forward) is None:
                await ctx.say("Не вижу открытого плеера.")
        except media.MediaUnavailable:
            await self._do(ctx, control.media, "next" if forward else "previous")

    @command(
        [
            "(следующий|следующая) (трек|песня|композиция)",
            "(переключи|переключить|пропусти|пропустить|смени|сменить) [эту|этот] "
            "(трек|песню|композицию)",
        ]
    )
    async def next_track(self, ctx: Context) -> None:
        await self._skip(ctx, True)

    @command(["(предыдущий|предыдущая) (трек|песня|композиция)", "верни (трек|песню)"])
    async def previous_track(self, ctx: Context) -> None:
        await self._skip(ctx, False)

    @command(
        [
            "что [сейчас] (играет|за музыка|за песня|за трек)",
            "(какая|какой) [это] (песня|трек)",
            "кто (поет|исполняет)",
        ]
    )
    async def what_plays(self, ctx: Context) -> None:
        await ctx.say(await self.now_playing())

    async def now_playing(self) -> str:
        try:
            player = await media.now_playing()
        except media.MediaUnavailable as e:
            return f"Не могу узнать: {e}."
        if player is None or not player.track:
            return "Сейчас ничего не играет."
        return f"{'Играет' if player.status == 'playing' else 'На паузе'}: {player.track}."

    # -- screen ---------------------------------------------------------------------------

    @command(["заблокируй (компьютер|экран)", "блокировка экрана"])
    async def lock(self, ctx: Context) -> None:
        await self._do(ctx, control.lock_screen)

    @command(["[сделай] (скриншот|снимок экрана)", "сфотографируй экран"])
    async def screenshot(self, ctx: Context) -> None:
        try:
            path = await asyncio.to_thread(control.screenshot)
        except OSError as e:
            await ctx.say(f"Не получилось сделать скриншот: {e}.")
            return
        await ctx.notify("Скриншот сохранён", str(path), "success")
        await ctx.say("Готово, скриншот в папке «Изображения».")

    # -- power ----------------------------------------------------------------------------

    @command(
        ["(выключи|выруби) (компьютер|пк)", "заверши работу"],
        dangerous=True,
        description="выключить компьютер",
    )
    async def shutdown(self, ctx: Context) -> None:
        await self._do(ctx, control.power, "shutdown", ok=_POWER_DONE["shutdown"])

    @command(
        ["перезагрузи (компьютер|пк)", "перезагрузка"],
        dangerous=True,
        description="перезагрузить компьютер",
    )
    async def restart(self, ctx: Context) -> None:
        await self._do(ctx, control.power, "restart", ok=_POWER_DONE["restart"])

    @command(
        ["(усыпи|отправь в сон) (компьютер|пк)", "спящий режим"],
        dangerous=True,
        description="перевести компьютер в сон",
    )
    async def sleep(self, ctx: Context) -> None:
        await self._do(ctx, control.power, "sleep", ok=_POWER_DONE["sleep"])

    @command(["отмени (выключение|перезагрузку)"], priority=5)
    async def cancel_power(self, ctx: Context) -> None:
        await self._do(ctx, control.power, "cancel", ok=_POWER_DONE["cancel"])

    # -- LLM tools ------------------------------------------------------------------------

    @tool("Установить громкость системы в процентах")
    async def set_volume(self, percent: Annotated[int, "0-100"]) -> str:
        return f"Громкость {await asyncio.to_thread(control.set_volume, percent)}%"

    @tool("Сделать громче или тише на сколько-то процентов")
    async def change_volume(
        self, delta: Annotated[int, "Изменение в процентах: +10 громче, -10 тише"]
    ) -> str:
        level = await asyncio.to_thread(control.change_volume, abs(delta), delta > 0)
        return f"Громкость {level}%" if level is not None else "Готово"

    @tool("Узнать текущую громкость системы")
    async def get_volume(self) -> str:
        level = await asyncio.to_thread(control.get_volume)
        return f"Громкость {level}%" if level is not None else "Неизвестно"

    @tool(
        "Включить или продолжить музыку. Если пользователь назвал приложение (Яндекс Музыка, "
        "Spotify…), передай его: оно откроется само"
    )
    async def play_music_tool(
        self,
        app: Annotated[
            str, "Приложение, только если пользователь сам его назвал; не придумывай — иначе пусто"
        ] = "",
    ) -> str:
        return await self.play_music(app) or "Нажата клавиша воспроизведения"

    @tool("Пауза, следующий или предыдущий трек")
    async def media_control(self, action: Literal["pause", "next", "previous"]) -> str:
        try:
            if action == "pause":
                return "Поставлено на паузу" if await media.pause() else "Ничего не играет"
            player = await media.skip(action == "next")
            return "Переключено" if player else "Нет открытого плеера"
        except media.MediaUnavailable:
            key = "play_pause" if action == "pause" else action
            await asyncio.to_thread(control.media, key)
            return "Готово"

    @tool("Что сейчас играет: исполнитель и название трека")
    async def what_is_playing(self) -> str:
        return await self.now_playing()

    @tool("Сделать скриншот экрана и сохранить в папку «Изображения»")
    async def take_screenshot(self) -> str:
        return f"Сохранено: {await asyncio.to_thread(control.screenshot)}"

    @tool("Выключить, перезагрузить, усыпить компьютер или отменить выключение", dangerous=True)
    async def power_control(self, action: PowerAction) -> str:
        await asyncio.to_thread(control.power, action)
        return _POWER_DONE[action]


def _started(player: media.Player) -> str:
    if player.status == "playing":
        return f"Уже играет: {player.track}." if player.track else "Музыка уже играет."
    return f"Включаю: {player.track}." if player.track else "Включаю."
