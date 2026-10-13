"""Minimal Telegram Bot API client over httpx (long polling only).

The bot token is part of every request URL, so it must never reach a log: every error is
rewritten by :meth:`BotApi.redact` before it leaves this module.
"""

from __future__ import annotations

from typing import Any

import httpx

API_URL = "https://api.telegram.org"
POLL_TIMEOUT = 25  # seconds the server holds a getUpdates request
MESSAGE_LIMIT = 4096  # Telegram's hard limit for one message


class TelegramError(Exception):
    """A failed Bot API call; the message never contains the token."""

    def __init__(self, message: str, *, code: int = 0, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

    @property
    def fatal(self) -> bool:
        """The token is rejected: retrying quickly is pointless."""
        return self.code in (401, 404)


class BotApi:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=f"{API_URL}/bot{token}/",
            timeout=httpx.Timeout(POLL_TIMEOUT + 15, connect=10),
            transport=transport,
        )

    def redact(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        """Call a Bot API method and return ``result``; raise :class:`TelegramError`."""
        body = {k: v for k, v in params.items() if v is not None}
        try:
            response = await self._client.post(method, json=body)
        except httpx.HTTPError as e:
            raise TelegramError(self.redact(f"{type(e).__name__}: {e}")) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code != 200 or not data.get("ok"):
            parameters = data.get("parameters") or {}
            description = str(data.get("description") or f"HTTP {response.status_code}")
            raise TelegramError(
                self.redact(f"{method}: {description}"),
                code=int(data.get("error_code") or response.status_code),
                retry_after=float(parameters.get("retry_after") or 0),
            )
        return data.get("result")

    async def get_updates(self, offset: int | None, timeout: int = POLL_TIMEOUT) -> list[Any]:
        result = await self.call(
            "getUpdates",
            offset=offset,
            timeout=timeout,
            allowed_updates=["message", "callback_query"],
        )
        return list(result or [])


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split ``text`` into parts of at most ``limit`` chars, preferring line/space breaks."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts
