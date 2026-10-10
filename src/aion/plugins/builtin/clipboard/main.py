"""Reading, translating and summarizing the clipboard."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from typing import Annotated

from aion.sdk import Context, Plugin, command, tool

LANGUAGES = {
    "английский": "английский", "английском": "английский", "русский": "русский",
    "русском": "русский", "немецкий": "немецкий", "французский": "французский",
    "испанский": "испанский", "китайский": "китайский", "японский": "японский",
    "итальянский": "итальянский", "украинский": "украинский",
}  # fmt: skip


def read_clipboard() -> str:
    if sys.platform == "win32":
        return _read_windows()
    for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["pbpaste"]):
        if shutil.which(cmd[0]):
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
            return out.stdout
    raise RuntimeError("нет утилиты для чтения буфера (wl-paste, xclip или pbpaste)")


def _read_windows() -> str:
    import ctypes
    from ctypes import wintypes

    cf_unicodetext = 13
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    for _ in range(10):  # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        import time

        time.sleep(0.05)
    else:
        raise RuntimeError("буфер обмена занят другой программой")
    try:
        handle = user32.GetClipboardData(cf_unicodetext)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class Clipboard(Plugin):
    async def _text(self, ctx: Context) -> str | None:
        try:
            text = (await asyncio.to_thread(read_clipboard)).strip()
        except RuntimeError as e:
            await ctx.say(f"Не могу прочитать буфер обмена: {e}.")
            return None
        if not text:
            await ctx.say("В буфере обмена нет текста.")
            return None
        return text

    @command(
        [
            "прочитай (что скопировано|скопированное|буфер обмена|буфер)",
            "что (в буфере обмена|я скопировал)",
        ]
    )
    async def read(self, ctx: Context) -> None:
        text = await self._text(ctx)
        if text is None:
            return
        limit = int(self.config["max_chars"])
        tail = " И дальше ещё." if len(text) > limit else ""
        await ctx.say(text[:limit] + tail)

    @command(
        [
            "переведи (скопированное|что скопировано|буфер обмена)",
            "переведи (скопированное|что скопировано|буфер обмена) на {language}",
        ],
        priority=2,
    )
    async def translate(self, ctx: Context, language: str = "") -> None:
        text = await self._text(ctx)
        if text is None:
            return
        if not ctx.llm.available:
            await ctx.say("Для перевода нужна языковая модель — включите её на странице LLM.")
            return
        target = LANGUAGES.get(language.strip(), language.strip()) or (
            "русский" if not any("а" <= c <= "я" for c in text.lower()) else "английский"
        )
        await ctx.set_emotion("thinking")
        result = await ctx.llm.complete(
            text[:4000],
            system=f"Переведи текст на {target} язык. Выведи только перевод, без пояснений.",
            max_tokens=1500,
        )
        await ctx.say(result)

    @command(["(перескажи|кратко перескажи) (скопированное|что скопировано|буфер обмена)"])
    async def summarize(self, ctx: Context) -> None:
        text = await self._text(ctx)
        if text is None:
            return
        if not ctx.llm.available:
            await ctx.say("Для пересказа нужна языковая модель.")
            return
        result = await ctx.llm.complete(
            text[:8000],
            system="Перескажи текст по-русски в 2–3 предложениях для озвучивания голосом.",
            max_tokens=400,
        )
        await ctx.say(result)

    @tool("Прочитать текст из буфера обмена пользователя")
    async def get_clipboard(self, max_chars: Annotated[int, "Ограничение длины"] = 4000) -> str:
        return (await asyncio.to_thread(read_clipboard))[:max_chars] or "(пусто)"
