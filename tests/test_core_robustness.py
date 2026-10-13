"""Robustness of the core: user-facing errors, races, bounded buffers, safe shutdown."""

from __future__ import annotations

import asyncio
import errno
import json
import socket
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import EventBus, NullOutput
from aion.core.dialog import Turn
from aion.core.errors import NO_INTERNET, is_network_error, user_message
from aion.core.events import StateChanged
from aion.core.initiative import MAX_COOLDOWNS, MAX_PENDING, Observation
from aion.llm.base import LlmError, LlmProvider, Message, StreamEvent, TextDelta
from aion.llm.brain import Brain
from aion.llm.ollama import OllamaProvider
from aion.storage import Database
from tests.helpers import say

# -- user-facing messages --------------------------------------------------------------


class PortAudioError(Exception):
    """Stand-in for sounddevice.PortAudioError (matched by name, the module is optional)."""


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        socket.gaierror(11001, "getaddrinfo failed"),
        ConnectionResetError(),
        OSError(errno.ENETUNREACH, "unreachable"),
    ],
)
def test_network_failures_say_there_is_no_internet(exc: Exception) -> None:
    assert is_network_error(exc)
    assert user_message(exc) == NO_INTERNET


def test_network_error_is_found_in_the_cause_chain() -> None:
    outer = RuntimeError("download failed")
    outer.__cause__ = httpx.ConnectError("boom")
    assert user_message(outer) == NO_INTERNET


@pytest.mark.parametrize(
    ("exc", "needle"),
    [
        (PortAudioError("Error opening InputStream: Device unavailable"), "занят"),
        (PortAudioError("Error querying device -1: Invalid device"), "не найден"),
        (PortAudioError("something else"), "микрофон"),
        (PermissionError(13, "denied", "C:/x/aion.db"), "C:/x/aion.db"),
        (FileNotFoundError(2, "no", "ollama.exe"), "ollama.exe"),
        (OSError(errno.ENOSPC, "full"), "места на диске"),
        (sqlite3.DatabaseError("file is not a database"), "База данных"),
        (LlmError("Ollama не установлена"), "Ollama не установлена"),
        (RuntimeError("Русское сообщение"), "Русское сообщение"),
    ],
)
def test_user_message_is_russian_and_specific(exc: Exception, needle: str) -> None:
    assert needle.lower() in user_message(exc).lower()


@pytest.mark.parametrize("exc", [KeyError("secret_key"), RuntimeError("English internals")])
def test_unknown_errors_do_not_leak_internals(exc: Exception) -> None:
    message = user_message(exc)
    assert "secret_key" not in message
    assert "English" not in message
    assert "журнал" in message


# -- brain: a provider bug is not a traceback ------------------------------------------


class Exploding(LlmProvider):
    name = "exploding"

    async def stream(self, *_a: Any, **_k: Any) -> AsyncIterator[StreamEvent]:
        raise KeyError("choices")
        yield TextDelta("")  # pragma: no cover - makes this an async generator


async def test_unexpected_provider_error_is_spoken_politely(app: Aion) -> None:
    app.llm = provider = Exploding()
    app.brain = Brain(app, provider)
    app.dialog.fallback = app.brain.respond
    spoken = await say(app, "расскажи что-нибудь")
    assert spoken
    assert all("choices" not in s and "KeyError" not in s for s in spoken)
    assert not app.dialog.busy


# -- ollama: server died between two questions -----------------------------------------


def _ollama_with(handler: Any) -> OllamaProvider:
    provider = OllamaProvider("http://127.0.0.1:11434", "m")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # pyright: ignore[reportPrivateUsage]
    return provider


async def _collect(provider: OllamaProvider) -> str:
    out = ""
    async for event in provider.stream(
        [Message("user", "hi")], system="", tools=[], temperature=0.1, max_tokens=10
    ):
        if isinstance(event, TextDelta):
            out += event.text
    return out


async def test_ollama_reconnects_once_when_the_server_died() -> None:
    chats = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chats
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0"})
        chats += 1
        if chats == 1:
            raise httpx.ConnectError("refused", request=request)
        lines = [{"message": {"content": "Привет"}}, {"done": True, "done_reason": "stop"}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    provider = _ollama_with(handler)
    provider._ready = True  # pyright: ignore[reportPrivateUsage]
    assert await _collect(provider) == "Привет"
    assert chats == 2
    await provider.aclose()


async def test_ollama_garbage_is_an_llm_error_not_a_traceback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={})
        return httpx.Response(200, content=b"<html>proxy login</html>")

    provider = _ollama_with(handler)
    with pytest.raises(LlmError, match="непонятный"):
        await _collect(provider)
    await provider.aclose()


async def test_ollama_read_timeout_is_explained() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={})
        raise httpx.ReadTimeout("slow", request=request)

    provider = _ollama_with(handler)
    with pytest.raises(LlmError, match="загружается"):
        await _collect(provider)
    await provider.aclose()


# -- event bus / dialog races ----------------------------------------------------------


async def test_emit_threadsafe_after_the_loop_closed_is_silent() -> None:
    bus = EventBus()
    loop = asyncio.new_event_loop()
    loop.close()
    bus.emit_threadsafe(StateChanged(old="idle", new="thinking"), loop)  # must not raise


async def test_two_quick_phrases_leave_only_the_last_turn_running(app: Aion) -> None:
    finished: list[str] = []

    async def slow(turn: Turn) -> None:
        await asyncio.sleep(0.15)
        finished.append(turn.text)

    app.dialog.fallback = slow
    await app.dialog.submit("первая")
    await asyncio.sleep(0)
    await asyncio.gather(app.dialog.submit("вторая"), app.dialog.submit("третья"))
    await app.dialog.wait()
    await asyncio.sleep(0.3)
    assert finished == ["третья"]


# -- bounded buffers -------------------------------------------------------------------


async def test_initiative_buffers_stay_bounded(app: Aion) -> None:
    for i in range(MAX_COOLDOWNS * 2):
        app.initiative.submit(
            Observation(f"событие {i}", importance=1.0, key=f"k{i}", cooldown=0.001, ttl=60)
        )
    assert len(app.initiative._pending) <= MAX_PENDING  # pyright: ignore[reportPrivateUsage]
    assert len(app.initiative._cooldowns) <= MAX_COOLDOWNS  # pyright: ignore[reportPrivateUsage]


async def test_diary_keeps_only_the_tail_of_a_never_ending_conversation(app: Aion) -> None:
    from aion.core.events import SpeechRecognized
    from aion.llm.episodes import MAX_LINES

    for i in range(MAX_LINES + 50):
        await app.bus.publish(SpeechRecognized(text=f"фраза {i}", source="text"))
    assert len(app.diary.lines) == MAX_LINES
    assert app.diary.lines[-1][2] == f"фраза {MAX_LINES + 49}"


# -- storage ---------------------------------------------------------------------------


async def test_database_uses_wal_and_fetchone_works(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.open()
    row = await db.fetchone("PRAGMA journal_mode")
    assert row is not None
    assert row["journal_mode"] == "wal"
    await db.kv("ns").set("a", {"x": 1})
    assert await db.kv("ns").get("a") == {"x": 1}
    assert await db.fetchone("SELECT 1 WHERE 0") is None
    await db.close()


async def test_database_open_failure_closes_the_connection(tmp_path: Path) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite file at all" * 100)
    db = Database(bad)
    with pytest.raises(sqlite3.DatabaseError):
        await db.open()
    with pytest.raises(RuntimeError):
        _ = db.conn


async def test_corrupt_database_gives_a_russian_startup_error(app_store: ConfigStore) -> None:
    path = app_store.config.paths.database
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"garbage" * 200)
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    with pytest.raises(RuntimeError, match="База данных"):
        await aion.start()


# -- shutdown --------------------------------------------------------------------------


async def test_stop_continues_after_a_failing_step(app_store: ConfigStore) -> None:
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    await aion.start()

    async def boom() -> None:
        raise RuntimeError("mcp exploded")

    aion.mcp.stop = boom  # type: ignore[method-assign]
    await aion.stop()
    with pytest.raises(RuntimeError, match="не открыта"):
        _ = aion.db.conn  # the database was still closed


async def test_background_tasks_are_cancelled_on_stop(app_store: ConfigStore) -> None:
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    await aion.start()
    gate = asyncio.Event()
    task = aion.spawn(gate.wait(), "never-ends")
    await aion.stop()
    assert task.cancelled()
    assert not aion._background  # pyright: ignore[reportPrivateUsage]


async def test_reload_llm_calls_do_not_overlap(app: Aion) -> None:
    running = 0
    peak = 0
    original = app._setup_llm  # pyright: ignore[reportPrivateUsage]

    def tracked() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        original()
        running -= 1

    app._setup_llm = tracked  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    await asyncio.gather(app.reload_llm(), app.reload_llm(), app.reload_llm())
    assert peak == 1


async def test_game_webhook_drops_a_stalled_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from aion.sdk import webhook

    monkeypatch.setattr(webhook, "READ_TIMEOUT_S", 0.2)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    async def handler(_payload: dict[str, Any]) -> None: ...

    server = asyncio.create_task(webhook.serve_json(port, handler))
    try:
        for _ in range(50):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except ConnectionRefusedError:
                await asyncio.sleep(0.02)
        else:
            raise AssertionError("the webhook server did not start")
        writer.write(b"POST / HTTP/1.1\r\nContent-Le")  # ...and never finishes the headers
        await writer.drain()
        reply = await asyncio.wait_for(reader.read(), 2)  # the server answers 400 and hangs up
        assert reply.startswith(b"HTTP/1.1 400")
        writer.close()
    finally:
        server.cancel()
