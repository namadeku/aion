"""Startup: slow plugins do not hold up the start, and nothing is lost by deferring them."""

from __future__ import annotations

from pathlib import Path

import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from aion.llm.base import Message, TextDelta, TurnEnd
from aion.llm.brain import Brain
from tests.helpers import say, write_plugin
from tests.test_llm import FakeProvider

SLOW = """
import asyncio
from aion.sdk import Plugin, command, tool

class Slow(Plugin):
    async def on_load(self) -> None:
        await asyncio.sleep(0.8)

    @command(["медленная команда"])
    async def go(self, ctx) -> None:
        await ctx.say("готово")

    @tool("Медленный инструмент")
    async def slow_tool(self) -> str:
        return "ok"
"""


def make(store: ConfigStore, *, defer: bool) -> Aion:
    aion = Aion(
        store,
        speech=lambda b, s, _c: NullOutput(b, s),
        watch_plugins=False,
        defer_slow_plugins=defer,
    )
    aion.plugins.load_grace = 0.1
    return aion


async def test_slow_plugin_is_loaded_in_the_background(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    write_plugin(plugins_dir, "slow", SLOW)
    aion = make(app_store, defer=True)
    async with aion:
        record = aion.plugins.records["slow"]
        assert record.status != "loaded"  # start did not wait for it
        assert aion.plugins.loaded()  # the quick ones are ready
        assert not any(c.plugin == "slow" for c in aion.plugins.commands())

        await aion.plugins.wait_loaded()
        assert record.status == "loaded"
        assert await say(aion, "медленная команда") == ["готово"]


async def test_without_deferring_start_waits_for_everything(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    write_plugin(plugins_dir, "slow", SLOW)
    async with make(app_store, defer=False) as aion:
        assert aion.plugins.records["slow"].status == "loaded"


async def test_stop_cancels_a_plugin_that_is_still_loading(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    write_plugin(plugins_dir, "slow", SLOW)
    aion = make(app_store, defer=True)
    await aion.start()
    record = aion.plugins.records["slow"]
    assert record.status != "loaded"
    await aion.stop()  # must neither hang nor raise
    assert record.status != "loaded"
    assert record.instance is None
    assert not aion.plugins._deferred  # pyright: ignore[reportPrivateUsage]


async def test_prompt_prefill_waits_for_plugins_still_loading(
    app_store: ConfigStore, plugins_dir: Path
) -> None:
    """The cached prompt prefix includes the tools: a late plugin must be in it."""
    write_plugin(plugins_dir, "slow", SLOW)
    aion = make(app_store, defer=True)
    async with aion:
        provider = FakeProvider(lambda _m, _t: [TextDelta("."), TurnEnd(Message("assistant", "."))])
        provider.prefix_cache = True  # pyright: ignore[reportAttributeAccessIssue]
        aion.llm = provider
        aion.brain = Brain(aion, provider)
        assert aion.plugins.records["slow"].status != "loaded"
        await aion.brain.warm_up()
        _, _, tools = provider.calls[-1]
        assert "slow__slow_tool" in {t.name for t in tools}


async def test_failed_voice_start_shows_a_readable_reason(
    app_store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy microphone must not reach the page as "PortAudioError: ..."."""
    from aion import runtime, voice
    from aion.tts.output import VoiceOutput

    class PortAudioError(Exception):
        pass

    async def no_pipeline(*_a: object) -> object:
        raise PortAudioError("Error opening InputStream: Device unavailable")

    class Page:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        def set_startup(self, stage: str, error: str | None = None) -> None:
            self.calls.append((stage, error))

    async def no_warm_up(self: VoiceOutput) -> None:
        return None

    monkeypatch.setattr(voice, "create_pipeline", no_pipeline)
    monkeypatch.setattr(VoiceOutput, "warm_up", no_warm_up)
    aion = Aion(
        app_store,
        speech=lambda b, s, _c: VoiceOutput(b, s, app_store, object()),  # pyright: ignore[reportArgumentType]
        watch_plugins=False,
    )
    page = Page()
    started = await runtime._start_voice(aion, page, lambda *_a: None)  # pyright: ignore[reportArgumentType, reportPrivateUsage]
    assert started is False
    stage, error = page.calls[-1]
    assert stage == "failed"
    assert error is not None
    assert "занят" in error
    assert "PortAudioError" not in error
