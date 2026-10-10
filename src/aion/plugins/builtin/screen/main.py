"""Questions about the screen: a screenshot goes to a local vision model in Ollama."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Annotated

import httpx

from aion.llm.streamtext import clean_for_speech
from aion.sdk import Context, Plugin, command, tool

from .capture import screenshot

TIMEOUT_S = 120.0  # the first call loads the model into the GPU
MAX_TOKENS = 300  # a spoken answer; also stops a model that rambles

STYLE = "Ответ прозвучит голосом: по-русски, 1–3 коротких предложения, без markdown и списков."
DESCRIBE = "Что сейчас на экране пользователя: какая программа открыта и чем он занят?"
READ = "Прочитай главный текст на экране и коротко перескажи его."
TRANSLATE = (
    "Найди на экране основной текст на иностранном языке и переведи его на русский. "
    "Ответь только переводом; если текста много — переведи самое важное."
)


class VisionError(RuntimeError):
    pass


class Screen(Plugin):
    async def look(self, question: str) -> str:
        """Take a screenshot and ask the vision model about it."""
        image = await asyncio.to_thread(screenshot, int(self.config["max_side"]))
        started = time.perf_counter()
        base_url = self.host.config.llm.ollama.base_url.rstrip("/")
        payload = {
            "model": self.config["model"],
            "messages": [
                {
                    "role": "user",
                    "content": f"{question}\n\n{STYLE}",
                    "images": [base64.b64encode(image).decode()],
                }
            ],
            "stream": False,
            "think": False,
            # unload right away: on an 8 GB GPU it has pushed the main model out of memory
            "keep_alive": 0,
            "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": MAX_TOKENS},
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                response = await client.post(f"{base_url}/api/chat", json=payload)
        except httpx.HTTPError as e:
            raise VisionError(f"Ollama недоступна: {e}") from e
        if response.status_code == 404:
            raise VisionError(
                f"Нет модели {self.config['model']}. "
                f"Скачайте её командой ollama pull {self.config['model']}."
            )
        if response.is_error:
            raise VisionError(f"Ollama: {response.text[:200]}")
        answer = str(response.json().get("message", {}).get("content", "")).strip()
        self.log.info("Экран разобран за {:.1f} с", time.perf_counter() - started)
        self.host.prewarm_llm()  # bring the main model back before the next question
        return clean_for_speech(answer) or "Не получилось разобрать экран."

    async def answer(self, ctx: Context, question: str) -> None:
        await ctx.set_emotion("thinking")
        await ctx.say("Секунду, смотрю.", wait=False)
        try:
            await ctx.say(await self.look(question))
        except VisionError as e:
            await ctx.say(str(e))

    @command(["(что|посмотри) [у меня] на экране", "что я (сейчас делаю|открыл)"])
    async def describe(self, ctx: Context) -> None:
        await self.answer(ctx, DESCRIBE)

    @command(["прочитай [что] [у меня] (на экране|с экрана)", "прочитай [этот] текст"])
    async def read(self, ctx: Context) -> None:
        await self.answer(ctx, READ)

    @command(
        ["переведи [этот] текст [на экране|с экрана]", "переведи (что на экране|с экрана|это)"]
    )
    async def translate(self, ctx: Context) -> None:
        await self.answer(ctx, TRANSLATE)

    @tool(
        "Посмотреть на экран пользователя и ответить на вопрос о нём: что открыто, что "
        "написано, перевести текст, объяснить ошибку или окно. Используй, когда пользователь "
        "говорит «это», «тут», «на экране» о том, что видит"
    )
    async def look_at_screen(
        self, question: Annotated[str, "Что узнать об экране, по-русски"]
    ) -> str:
        try:
            return await self.look(question)
        except VisionError as e:
            return f"Ошибка: {e}"
