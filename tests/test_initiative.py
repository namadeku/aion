from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from aion.app import Aion
from aion.core import DialogManager, NullOutput
from aion.core import initiative as initiative_module
from aion.core.dialog import Turn
from aion.core.initiative import Observation, min_gap, min_importance
from aion.llm.base import Message, StreamEvent, TextDelta, ToolDef, TurnEnd
from aion.llm.brain import Brain
from tests.test_llm import FakeProvider, text_reply


@pytest.fixture
def lively(app: Aion, monkeypatch: pytest.MonkeyPatch) -> Aion:
    """Talks about everything at once: no talkativeness filter, no gap, short gathering."""
    app.config.initiative.talkativeness = 1.0
    app.initiative.gather_s = 0.05
    monkeypatch.setattr(initiative_module, "min_gap", lambda _t: 0.0)
    return app


def spoken(app: Aion) -> list[str]:
    assert isinstance(app.speech, NullOutput)
    return app.speech.spoken


async def wait_spoken(app: Aion, count: int, timeout: float = 2.0) -> list[str]:
    async with asyncio.timeout(timeout):
        while len(spoken(app)) < count:
            await asyncio.sleep(0.01)
    return spoken(app)


def use_provider(app: Aion, provider: FakeProvider) -> FakeProvider:
    app.llm = provider
    app.brain = Brain(app, provider)
    app.dialog.fallback = app.brain.respond
    return provider


def test_talkativeness_scales() -> None:
    assert min_importance(0.5) == 0.5
    assert min_importance(1.0) == 0.0
    assert min_gap(1.0) < min_gap(0.5) < min_gap(0.0)


async def test_quick_phrase_without_llm(lively: Aion) -> None:
    assert lively.brain is None
    lively.initiative.submit(Observation("Победа в матче", importance=1.0, quick=["Победа!"]))
    assert await wait_spoken(lively, 1) == ["Победа!"]


async def test_unimportant_observation_is_dropped(app: Aion) -> None:
    app.config.initiative.talkativeness = 0.5
    assert not app.initiative.submit(Observation("убил крипа", importance=0.2, quick=["да"]))
    app.config.initiative.talkativeness = 0.0
    assert app.initiative.submit(Observation("эйс", importance=1.0, quick=["Эйс!"]))
    app.config.initiative.enabled = False
    assert not app.initiative.submit(Observation("эйс", importance=1.0, quick=["Эйс!"]))


async def test_cooldown_per_key(lively: Aion) -> None:
    obs = {"importance": 0.5, "key": "low_hp", "cooldown": 30, "quick": ["Осторожно!"]}
    assert lively.initiative.submit(Observation("мало здоровья", **obs))
    assert not lively.initiative.submit(Observation("снова мало здоровья", **obs))
    assert lively.initiative.submit(Observation("другое", importance=0.5, quick=["Ого"]))


async def test_stale_observation_is_dropped(lively: Aion) -> None:
    lively.initiative.submit(Observation("давно было", importance=0.5, ttl=0, quick=["Поздно"]))
    await asyncio.sleep(0.2)
    assert spoken(lively) == []


async def test_burst_is_one_llm_remark_remembered_in_dialog(lively: Aion) -> None:
    provider = use_provider(lively, FakeProvider(lambda _m, _t: text_reply("[joy] Трипл, браво!")))
    lively.initiative.submit(Observation("двойное убийство", importance=0.4, quick=["Дабл"]))
    lively.initiative.submit(Observation("тройное убийство", importance=0.8, quick=["Трипл"]))
    assert await wait_spoken(lively, 1) == ["Трипл, браво!"]
    await asyncio.sleep(0.1)
    assert len(provider.calls) == 1
    messages, _, _ = provider.calls[0]
    assert "двойное убийство" in messages[-1].content
    assert "тройное убийство" in messages[-1].content
    assert lively.brain is not None
    history = [m.content for m in lively.brain.memory.messages()]
    assert history[0] == "[событие] двойное убийство; тройное убийство"
    assert history[1].endswith("Трипл, браво!")


class SlowProvider(FakeProvider):
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, messages: list[Message], **kwargs: Any
    ) -> AsyncIterator[StreamEvent]:
        await asyncio.sleep(10)
        yield TextDelta("Слишком поздно.")
        yield TurnEnd(Message("assistant", "Слишком поздно."))


async def test_slow_llm_falls_back_to_quick_phrase(lively: Aion) -> None:
    lively.config.initiative.llm_timeout = 0.5
    use_provider(lively, SlowProvider(lambda _m, _t: []))
    lively.initiative.submit(Observation("эйс", importance=1.0, quick=["Эйс!"]))
    assert await wait_spoken(lively, 1, timeout=3) == ["Эйс!"]


async def test_llm_uses_cached_prompt_and_tools(lively: Aion) -> None:
    def script(_m: list[Message], tools: list[ToolDef]) -> list[StreamEvent]:
        assert tools  # same tool list as in dialog: the prompt prefix stays cached
        return text_reply("Ура!")

    provider = use_provider(lively, FakeProvider(script))
    await lively.dialog.submit("расскажи что-нибудь интересное")
    await lively.dialog.wait()
    lively.initiative.submit(Observation("победа", importance=1.0))
    await wait_spoken(lively, 2)
    assert provider.calls[0][1] == provider.calls[1][1]  # same system prompt


async def test_proactive_turn_is_interrupted_by_user(dialog: DialogManager) -> None:
    started = asyncio.Event()

    async def long_remark(turn: Turn) -> bool:
        started.set()
        await asyncio.sleep(10)
        return True

    remark = asyncio.create_task(dialog.proactive(long_remark, "событие"))
    await started.wait()
    assert not dialog.free
    await dialog.interrupt("speech")
    assert await remark is None
    assert dialog.free
