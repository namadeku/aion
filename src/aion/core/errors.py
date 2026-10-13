"""Turning exceptions into short Russian messages that are safe to show to the user.

The log keeps the full traceback; the user sees what happened and what to do, never a
stack trace or a bare ``ConnectError``.
"""

from __future__ import annotations

import errno
import socket
import sqlite3
from collections.abc import Iterator

from aion.llm.base import LlmError

NO_INTERNET = "Нет подключения к интернету. Проверьте сеть — офлайн-функции продолжат работать."

_NETWORK_NAMES = frozenset(
    {
        "ClientConnectorError",  # aiohttp (edge-tts)
        "ClientConnectionError",
        "ServerDisconnectedError",
        "ClientOSError",
        "APIConnectionError",  # anthropic / openai SDK
        "APITimeoutError",
        "ConnectError",
        "WebSocketError",
    }
)
_NETWORK_ERRNOS = frozenset(
    {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.ECONNREFUSED, errno.ETIMEDOUT}
)


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_network_error(exc: BaseException) -> bool:
    """Whether the failure is about the connection (no internet, DNS, refused, timeout)."""
    import httpx  # lazy: keeps this module free of a ~150 ms import

    for e in _chain(exc):
        if isinstance(e, httpx.TransportError | socket.gaierror | ConnectionError | TimeoutError):
            return True
        if isinstance(e, OSError) and e.errno in _NETWORK_ERRNOS:
            return True
        if type(e).__name__ in _NETWORK_NAMES:
            return True
    return False


def _audio_message(text: str) -> str:
    lowered = text.lower()
    if any(w in lowered for w in ("unavailable", "busy", "exclusive", "in use")):
        return "Микрофон занят другой программой. Закройте её или выберите другое устройство."
    if any(w in lowered for w in ("invalid device", "no default", "no device", "invalid number")):
        return "Микрофон не найден. Подключите его или выберите устройство в настройках."
    return "Не удалось открыть микрофон или динамики. Проверьте устройства в настройках звука."


def user_message(exc: BaseException) -> str:
    """A one- or two-sentence Russian description of ``exc`` for the UI or the console."""
    if isinstance(exc, LlmError):
        return str(exc)
    for e in _chain(exc):
        if type(e).__name__ == "PortAudioError":
            return _audio_message(str(e))
    if is_network_error(exc):
        return NO_INTERNET
    if isinstance(exc, PermissionError):
        where = exc.filename or "файлу"
        return f"Нет доступа к {where}. Закройте другие программы или проверьте права."
    if isinstance(exc, FileNotFoundError):
        return f"Не найден файл или программа: {exc.filename or exc}"
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Недостаточно места на диске."
    if isinstance(exc, sqlite3.DatabaseError):
        return "База данных повреждена или занята другой копией Aion. Закройте лишние окна."
    if isinstance(exc, ImportError):
        return f"Не хватает библиотеки: {exc.name or exc}. Переустановите Aion."
    if isinstance(exc, TimeoutError):
        return "Превышено время ожидания ответа."
    if isinstance(exc, ValueError | RuntimeError) and str(exc):
        text = str(exc)
        # our own messages are already Russian; anything else is not for the user
        if any("а" <= ch.lower() <= "я" for ch in text):
            return text
    return "Что-то пошло не так. Подробности записаны в журнал (папка logs)."
