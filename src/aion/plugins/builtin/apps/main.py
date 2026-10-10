"""Open programs, websites and folders; manage their windows; search the web.

Every reply about an action follows what really happened: "Открыла Telegram" only after
its window appeared, "не вижу окна" when there is nothing to close.
"""

from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path
from typing import Annotated

from rapidfuzz import fuzz

from aion.sdk import Context, Plugin, command, tool

from .launcher import (
    SPOKEN_APPS,
    WINDOWS_APPS,
    Target,
    installed_apps,
    open_target,
    parse_aliases,
    resolve,
    search_url,
    start_menu_shortcuts,
    translit,
)
from .windows import (
    Window,
    close_window,
    focus_window,
    list_windows,
    minimize_all,
    minimize_window,
    paste_text,
)

WINDOW_WAIT_S = 6.0  # how long a launched app may take to show its window
WINDOW_MATCH = 80  # fuzzy score for "блокнот" ~ "Безымянный — Блокнот"


def display_name(target: Target) -> str:
    return target.title.title() if target.kind in ("appid", "shortcut") else target.title


def window_score(spoken: str, window: Window) -> float:
    """How well a spoken name fits a window (its title or its program)."""
    name = spoken.lower().strip()
    queries = {name, SPOKEN_APPS.get(name, name), translit(name), WINDOWS_APPS.get(name, name)}
    title = window.title.lower()
    return max(
        max(fuzz.partial_ratio(q, title) if len(q) >= 3 else 0.0, fuzz.ratio(q, window.process))
        for q in queries
    )


_NOT_NAMES = {"ка", "этот", "эту", "это", "окно", "окошко", "мне", "пожалуйста", "уже", "все"}


def name_spans(spoken: str) -> list[str]:
    """The phrase and its 1-3 word pieces: "ка этот блокнот он мне не нужен" has "блокнот"."""
    words = [w for w in spoken.lower().replace("-", " ").split() if w not in _NOT_NAMES]
    spans = [" ".join(words)] if words else []
    for size in (3, 2, 1):
        spans += [" ".join(words[i : i + size]) for i in range(len(words) - size + 1)]
    return list(dict.fromkeys(s for s in spans if len(s) >= 2))


def find_window(spoken: str, windows: list[Window]) -> Window | None:
    """The best matching window; on a tie the topmost one (windows come in z-order)."""
    best: tuple[float, Window] | None = None
    for window in windows:
        score = max((window_score(span, window) for span in name_spans(spoken)), default=0.0)
        if score >= WINDOW_MATCH and (best is None or score > best[0]):
            best = (score, window)
    return best[1] if best else None


async def wait_for_window(target: Target, before: set[int], timeout: float) -> Window | None:
    """A window that appeared after a launch, or the app's window that came to the front."""
    for _ in range(int(timeout / 0.3)):
        await asyncio.sleep(0.3)
        windows = await asyncio.to_thread(list_windows)
        new = [w for w in windows if w.hwnd not in before]
        if new:
            return new[0]
        if windows and window_score(target.title, windows[0]) >= WINDOW_MATCH:
            return windows[0]  # it was running already and just got focus
    return None


class Apps(Plugin):
    async def on_load(self) -> None:
        self._shortcuts: dict[str, Path] = {}
        self._apps: dict[str, str] = {}
        await self.rescan_apps()

    async def rescan_apps(self) -> int:
        self._shortcuts, self._apps = await asyncio.gather(
            asyncio.to_thread(start_menu_shortcuts), asyncio.to_thread(installed_apps)
        )
        self.log.debug("Приложений: {}, ярлыков: {}", len(self._apps), len(self._shortcuts))
        return len(self._apps) or len(self._shortcuts)

    def _resolve(self, name: str) -> Target | None:
        aliases = parse_aliases(str(self.config["aliases"]))
        return resolve(name, aliases, self._shortcuts, self._apps)

    async def launch(self, target: Target) -> str:
        """Open the target and report honestly whether it showed up."""
        before = {w.hwnd for w in await asyncio.to_thread(list_windows)}
        await asyncio.to_thread(open_target, target)
        name = display_name(target)
        if target.kind == "site":
            return f"Открываю {name} в браузере."
        if not before and not await asyncio.to_thread(list_windows):
            return f"Запускаю {name}."  # no window list on this system: can't check
        if await wait_for_window(target, before, WINDOW_WAIT_S) is not None:
            return f"{self.g('Открыл', 'Открыла')} {name}."
        return f"Запускаю {name}, но окно пока не появилось — может, ещё загружается."

    # -- open -------------------------------------------------------------------------------

    @command(
        [
            "(открой|открою|открыть|открывай|запусти|запустить|включи) {name}",
            "(открой|запусти) [мне] {name}",
        ]
    )
    async def open(self, ctx: Context, name: str) -> bool:
        target = self._resolve(name)
        if target is None:
            return False  # maybe another plugin (or the LLM) knows what this is
        await ctx.say(await self.launch(target))
        return True

    @command(
        [
            "(найди|поищи|загугли) [в интернете] {query}",
            "(найди|поищи) в (гугле|яндексе|интернете) {query}",
        ]
    )
    async def search(self, ctx: Context, query: str) -> None:
        webbrowser.open(search_url(str(self.config["search_engine"]), query))
        await ctx.say(f"Ищу: {query}.")

    @command(["обнови список программ"])
    async def rescan(self, ctx: Context) -> None:
        found = await self.rescan_apps()
        await ctx.say(f"{ctx.g('Нашёл', 'Нашла')} {found} программ.")

    # -- windows ----------------------------------------------------------------------------

    async def _window(self, name: str) -> Window | None:
        return find_window(name, await asyncio.to_thread(list_windows))

    async def close(self, name: str) -> str:
        window = await self._window(name)
        if window is None:
            return f"Не вижу открытого окна «{name}»."
        await asyncio.to_thread(close_window, window)
        return f"{self.g('Закрыл', 'Закрыла')} {window.title}."

    async def minimize(self, name: str) -> str:
        if not name:
            await asyncio.to_thread(minimize_all)
            return f"{self.g('Свернул', 'Свернула')} все окна."
        window = await self._window(name)
        if window is None:
            return f"Не вижу открытого окна «{name}»."
        await asyncio.to_thread(minimize_window, window)
        return f"{self.g('Свернул', 'Свернула')} {window.title}."

    async def focus(self, name: str) -> str:
        window = await self._window(name)
        if window is None:
            return f"Не вижу открытого окна «{name}»."
        await asyncio.to_thread(focus_window, window)
        return f"Переключаю на {window.title}."

    async def type_into(self, text: str, app: str = "") -> str:
        """Type text into a window of ``app`` (opening it if needed) or the active one."""
        window: Window | None = None
        if app:
            window = await self._window(app)
            if window is None and (target := self._resolve(app)) is not None:
                before = {w.hwnd for w in await asyncio.to_thread(list_windows)}
                await asyncio.to_thread(open_target, target)
                window = await wait_for_window(target, before, WINDOW_WAIT_S)
            if window is None:
                return f"Не вижу окна «{app}», куда писать."
            if not await asyncio.to_thread(focus_window, window):
                return f"Не получилось переключиться на {window.title}, ничего не напечатано."
        try:
            await asyncio.to_thread(paste_text, text)
        except OSError as e:
            return f"Не получилось напечатать: {e}."
        where = f" в {window.title}" if window else ""
        return f"{self.g('Напечатал', 'Напечатала')}{where}: {text}"

    def _split_app_and_text(self, rest: str) -> tuple[str, str] | None:
        """ "блокноте привет как дела" -> ("блокноте", "привет как дела")."""
        words = rest.split()
        windows = list_windows()
        for size in range(1, min(3, len(words) - 1) + 1):
            app = " ".join(words[:size])
            if find_window(app, windows) or self._resolve(app):
                return app, " ".join(words[size:])
        return None

    @command(["(закрой|закрыть|закрою) [окно] {name}"])
    async def close_cmd(self, ctx: Context, name: str) -> bool:
        if await self._window(name) is None and self._resolve(name) is None:
            return False  # "закрой таймер" and the like belong to other plugins
        await ctx.say(await self.close(name))
        return True

    @command(["сверни все [окна]", "покажи рабочий стол"], priority=1)
    async def minimize_all_cmd(self, ctx: Context) -> None:
        await ctx.say(await self.minimize(""))

    @command(["сверни [окно] {name}"])
    async def minimize_cmd(self, ctx: Context, name: str) -> None:
        await ctx.say(await self.minimize(name))

    @command(["(переключись|перейди) (на|в) {name}", "(покажи|разверни) [окно] {name}"])
    async def focus_cmd(self, ctx: Context, name: str) -> bool:
        if await self._window(name) is None:
            return False
        await ctx.say(await self.focus(name))
        return True

    @command(["(напиши|напечатай|набери|введи) в {rest}"], priority=1)
    async def type_in_cmd(self, ctx: Context, rest: str) -> bool:
        split = await asyncio.to_thread(self._split_app_and_text, rest)
        if split is None:
            return False  # "напиши в заметки ..." is for the notes plugin
        app, text = split
        await ctx.say(await self.type_into(text, app))
        return True

    @command(["(напечатай|набери) {text}"])
    async def type_cmd(self, ctx: Context, text: str) -> None:
        await ctx.say(await self.type_into(text))

    # -- LLM tools ----------------------------------------------------------------------------

    @tool("Открыть программу, сайт или папку по названию (как назвал пользователь)")
    async def open_app(self, name: Annotated[str, "Например: телеграм, ютуб, загрузки"]) -> str:
        target = self._resolve(name)
        if target is None:
            return f"Не найдено: «{name}»"
        return await self.launch(target)

    @tool("Закрыть окно программы по названию")
    async def close_app(
        self, name: Annotated[str, "Программа или окно, например «блокнот»"]
    ) -> str:
        return await self.close(name)

    @tool("Свернуть окно программы, или все окна, если name пустое")
    async def minimize_app(self, name: Annotated[str, "Программа или пусто"] = "") -> str:
        return await self.minimize(name)

    @tool("Переключиться на окно открытой программы (вывести на передний план)")
    async def switch_to_app(self, name: Annotated[str, "Программа или окно"]) -> str:
        return await self.focus(name)

    @tool(
        "Напечатать текст в окне программы, как с клавиатуры (откроет её, если закрыта); "
        "без app — в активное окно"
    )
    async def type_text_in(
        self,
        text: Annotated[str, "Что напечатать"],
        app: Annotated[str, "Программа, например «блокнот», или пусто"] = "",
    ) -> str:
        return await self.type_into(text, app)

    @tool("Список открытых окон: какие программы сейчас открыты")
    async def list_open_windows(self) -> str:
        windows = await asyncio.to_thread(list_windows)
        return "; ".join(f"{w.title} ({w.process})" for w in windows) or "Открытых окон нет"

    @tool("Открыть веб-страницу по адресу")
    async def open_url(self, url: Annotated[str, "Полный адрес https://..."]) -> str:
        if not url.startswith(("http://", "https://")):
            return "Нужен адрес, начинающийся с http:// или https://"
        webbrowser.open(url)
        return "Открыто в браузере"

    @tool("Открыть поиск в интернете по запросу (результаты увидит пользователь в браузере)")
    async def web_search(self, query: str) -> str:
        webbrowser.open(search_url(str(self.config["search_engine"]), query))
        return "Поиск открыт в браузере"
