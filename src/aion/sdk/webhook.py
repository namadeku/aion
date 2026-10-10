"""A tiny local HTTP receiver for JSON pushed by games (Game State Integration of CS2, Dota 2).

Runs inside a plugin task, so hot reload closes the port::

    async def on_load(self) -> None:
        self.create_task(serve_json(3000, self.on_state))
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

JsonHandler = Callable[[dict[str, Any]], Awaitable[None]]

MAX_BODY = 1 << 20
_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


async def serve_json(port: int, handler: JsonHandler, host: str = "127.0.0.1") -> None:
    """Accept ``POST`` requests with a JSON object body and pass each object to ``handler``."""

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            body = await _read_body(reader)
            payload = json.loads(body) if body else None
            if not isinstance(payload, dict):
                writer.write(_BAD)
                return
            writer.write(_OK)  # answer first: the game must not wait for our reaction
            await writer.drain()
            await handler(payload)  # pyright: ignore[reportUnknownArgumentType]
        except (ValueError, asyncio.IncompleteReadError, ConnectionError):
            writer.write(_BAD)
        except Exception:
            logger.exception("Ошибка обработки данных игры")
        finally:
            writer.close()

    server = await asyncio.start_server(client, host, port)
    logger.info("Жду данные игры на http://{}:{}", host, port)
    async with server:
        await server.serve_forever()


async def _read_body(reader: asyncio.StreamReader) -> bytes:
    head = await reader.readuntil(b"\r\n\r\n")
    length = 0
    for line in head.decode("latin-1").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if not 0 <= length <= MAX_BODY:
        raise ValueError("body too large")
    return await reader.readexactly(length)
