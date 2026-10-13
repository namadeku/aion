"""The voice pipeline tells the dialog whether a phrase was addressed by name (prefix mode)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.test_audio import frames, spoken, voice, wait_for  # noqa: F401 - the fixture

Submitted = list[tuple[str, bool | None]]


@pytest.fixture
def submitted(voice: Any, monkeypatch: pytest.MonkeyPatch) -> Submitted:  # noqa: F811
    aion = voice[0]
    calls: Submitted = []
    original = aion.dialog.submit

    async def record(text: str, source: Any = "text", *, named: bool | None = None) -> None:
        calls.append((text, named))
        await original(text, source, named=named)

    monkeypatch.setattr(aion.dialog, "submit", record)
    return calls


async def test_name_in_the_phrase(voice: Any, submitted: Submitted) -> None:  # noqa: F811
    aion, _pipeline, mic, _stt = voice
    mic.feed(
        frames(0.2, speech=False) + frames(1.5, speech=True, tag=1) + frames(0.6, speech=False)
    )
    await wait_for(lambda: spoken(aion))
    assert submitted == [("который час?", True)]


async def test_name_alone_then_the_command(voice: Any, submitted: Submitted) -> None:  # noqa: F811
    aion, _pipeline, mic, _stt = voice
    mic.feed(frames(0.6, speech=True, tag=3) + frames(0.6, speech=False))
    await wait_for(lambda: aion.state.state == "listening")
    mic.feed(frames(0.8, speech=True, tag=4) + frames(0.6, speech=False))
    await wait_for(lambda: spoken(aion))
    assert submitted == [("сколько времени", True)]


async def test_push_to_talk_counts_as_named(voice: Any, submitted: Submitted) -> None:  # noqa: F811
    aion, pipeline, mic, _stt = voice
    await pipeline.push_to_talk()
    mic.feed(frames(0.8, speech=True, tag=2) + frames(0.6, speech=False))
    await wait_for(lambda: spoken(aion))
    assert submitted == [("который час", True)]


async def test_an_answer_to_a_question_is_not_named(voice: Any, submitted: Submitted) -> None:  # noqa: F811
    aion, _pipeline, mic, _stt = voice
    task = asyncio.create_task(aion.dialog.ask("Какой город?", timeout=5))
    await wait_for(lambda: aion.dialog.awaiting_answer)
    mic.feed(frames(0.8, speech=True, tag=2) + frames(0.6, speech=False))
    assert await task == "который час"
    assert submitted == [("который час", False)]
