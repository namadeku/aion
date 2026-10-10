"""Ollama (local models) via its native ``/api/chat`` streaming API."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from aion.llm.base import (
    LlmError,
    LlmProvider,
    Message,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolDef,
    TurnEnd,
)
from aion.models import Progress

KEEP_ALIVE = "30m"  # loading 8B weights into VRAM takes tens of seconds; keep them there


class OllamaProvider(LlmProvider):
    name = "ollama"
    # Ollama reuses the KV cache of an identical prompt prefix: Brain.warm_up prefills it
    prefix_cache = True

    def __init__(self, base_url: str, model: str, num_ctx: int = 8192) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        # The same context size in every request: a different num_ctx reloads the model,
        # and a too small one truncates the prompt (system + tools), which kills the cache.
        self.num_ctx = num_ctx
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=300))
        self._server_lock = asyncio.Lock()
        self._started_server = False
        self.progress: Progress | None = None  # set by the app: pull a missing model with progress

    async def _alive(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/api/version", timeout=2)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_server(self) -> None:
        """Start ``ollama serve`` in the background (no window) if nothing listens yet.

        This way the Ollama desktop app does not have to run: Aion owns the server.
        """
        async with self._server_lock:
            if await self._alive():
                return
            if not self._is_local():
                raise LlmError(f"Ollama недоступна по адресу {self.base_url}")
            exe = find_ollama()
            if exe is None:
                raise LlmError("Ollama не установлена: https://ollama.com")
            if not self._started_server:
                logger.info("Запускаю сервер Ollama в фоне: {}", exe)
                flags = 0
                if sys.platform == "win32":
                    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                # cwd: the server outlives Aion and would otherwise lock the install folder,
                # so the installer could not update or remove it
                subprocess.Popen(
                    [str(exe), "serve"],
                    cwd=exe.parent,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                    close_fds=True,
                )
                self._started_server = True
            for _ in range(60):  # GPU discovery takes ~10-15 s on the first start
                if await self._alive():
                    return
                await asyncio.sleep(0.5)
            raise LlmError("Сервер Ollama не запустился за 30 секунд")

    async def warm_up(self) -> None:
        """Start the server if needed and load the model into memory ahead of the first question.

        In the app (``progress`` is set) a missing model is pulled first: the installer only
        installs Ollama itself.
        """
        await self.ensure_server()
        r = await self._load_model()
        if r.status_code == 404 and self.progress is not None:
            await self.pull()
            r = await self._load_model()
        if r.status_code == 404:
            raise LlmError(f"Модель {self.model} не найдена: выполните ollama pull {self.model}")
        logger.info("Модель {} загружена в память", self.model)

    async def _load_model(self) -> httpx.Response:
        return await self._client.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_ctx": self.num_ctx},
            },
            timeout=httpx.Timeout(10, read=300),
        )

    async def pull(self) -> None:
        """Download the model (``ollama pull``), reporting the bytes of all its layers."""
        label = f"Языковая модель {self.model}"
        layers: dict[str, tuple[int, int]] = {}
        logger.info("Скачиваю модель Ollama {}", self.model)
        async with self._client.stream(
            "POST",
            f"{self.base_url}/api/pull",
            json={"model": self.model},
            timeout=httpx.Timeout(10, read=600),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if error := chunk.get("error"):
                    raise LlmError(f"Не удалось скачать модель {self.model}: {error}")
                if (digest := chunk.get("digest")) and chunk.get("total"):
                    layers[digest] = (int(chunk.get("completed", 0)), int(chunk["total"]))
                    if self.progress:
                        done = sum(d for d, _ in layers.values())
                        total = sum(t for _, t in layers.values())
                        self.progress(
                            label, min(done, total - 1), total
                        )  # not done until "success"
        if self.progress:
            total = max(1, sum(t for _, t in layers.values()))
            self.progress(label, total, total)
        logger.info("Модель {} скачана", self.model)

    def serves_same_model(self, other: LlmProvider | None) -> bool:
        """Whether ``other`` uses the very model this provider keeps loaded."""
        return (
            isinstance(other, OllamaProvider)
            and other.base_url == self.base_url
            and other.model == self.model
        )

    async def unload(self) -> None:
        """Free the model's memory now instead of after KEEP_ALIVE."""
        try:
            await self._client.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=10,
            )
        except httpx.HTTPError:
            return  # the server is gone, and the memory with it
        logger.info("Модель {} выгружена из памяти", self.model)

    def _is_local(self) -> bool:
        host = httpx.URL(self.base_url).host
        return host in {"127.0.0.1", "localhost", "::1"}

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _convert(messages: list[Message], system: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "content": m.content})
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content,
                        "tool_calls": [
                            {"function": {"name": c.name, "arguments": c.arguments}}
                            for c in m.tool_calls
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str,
        tools: list[ToolDef],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert(messages, system),
            "stream": True,
            "think": False,  # reasoning models (qwen3...): answer right away, latency matters
            "keep_alive": KEEP_ALIVE,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        await self.ensure_server()
        text: list[str] = []
        calls: list[ToolCall] = []
        stop = ""
        try:
            async with self._client.stream("POST", f"{self.base_url}/api/chat", json=body) as r:
                if r.status_code == 404:
                    raise LlmError(
                        f"Модель {self.model} не найдена: выполните ollama pull {self.model}"
                    )
                if r.status_code >= 400:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    raise LlmError(f"Ollama ответила {r.status_code}: {detail}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if error := chunk.get("error"):
                        raise LlmError(f"Ollama: {error}")
                    msg = chunk.get("message") or {}
                    if delta := msg.get("content"):
                        text.append(delta)
                        yield TextDelta(delta)
                    for call in msg.get("tool_calls") or []:
                        fn = call.get("function", {})
                        args = fn.get("arguments") or {}
                        if isinstance(args, str):
                            args = json.loads(args or "{}")
                        calls.append(ToolCall(uuid.uuid4().hex[:12], fn.get("name", ""), args))
                    if chunk.get("done"):
                        stop = chunk.get("done_reason", "stop")
        except httpx.ConnectError as e:
            raise LlmError(
                "Ollama не запущена (ollama serve) или недоступна по адресу " + self.base_url
            ) from e
        except httpx.HTTPError as e:
            raise LlmError(f"Ошибка связи с Ollama: {e}") from e
        yield TurnEnd(Message("assistant", "".join(text), tool_calls=calls), stop)


def find_ollama() -> Path | None:
    if found := shutil.which("ollama"):
        return Path(found)
    if sys.platform == "win32":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return candidate
    return None
