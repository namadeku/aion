from __future__ import annotations

import asyncio
from typing import Any

from aion.core import CommandSpec, DialogManager, NullOutput, Turn
from aion.core.events import IntentMatched, SpeechRecognized


async def _settle(dialog: DialogManager) -> None:
    for _ in range(50):
        await asyncio.sleep(0)
    await dialog.wait()


def add(dialog: DialogManager, name: str, pattern: str, handler: Any, **kw: Any) -> None:
    specs = [
        *dialog.router.commands,
        CommandSpec(plugin="t", name=name, patterns=(pattern,), handler=handler, **kw),
    ]
    dialog.router.set_commands(specs)


async def test_command_is_routed(dialog: DialogManager, speech: NullOutput) -> None:
    events: list[str] = []
    dialog.bus.subscribe("*", lambda e: events.append(e.type))

    async def hello(turn: Turn) -> None:
        await turn.say("Здравствуйте")

    add(dialog, "hello", "привет", hello)
    await dialog.submit("Привет!")
    await _settle(dialog)
    assert speech.spoken == ["Здравствуйте"]
    assert SpeechRecognized.type in events
    assert IntentMatched.type in events
    assert dialog.state.state == "idle"


async def test_slots_are_converted(dialog: DialogManager, speech: NullOutput) -> None:
    got: list[int] = []

    async def timer(turn: Turn, minutes: int) -> None:
        got.append(minutes)

    add(dialog, "timer", "таймер на {minutes} минут", timer, slot_types={"minutes": int})
    await dialog.submit("таймер на пять минут")
    await _settle(dialog)
    assert got == [5]


async def test_fallback_when_no_command(dialog: DialogManager, speech: NullOutput) -> None:
    seen: list[str] = []

    async def fallback(turn: Turn) -> None:
        seen.append(turn.text)

    dialog.fallback = fallback
    await dialog.submit("в чём смысл жизни")
    await _settle(dialog)
    assert seen == ["в чём смысл жизни"]


async def test_unknown_without_fallback(dialog: DialogManager, speech: NullOutput) -> None:
    await dialog.submit("абракадабра")
    await _settle(dialog)
    assert speech.spoken == ["Я пока не знаю такой команды."]


async def test_ask_gets_next_utterance(dialog: DialogManager, speech: NullOutput) -> None:
    answers: list[str | None] = []

    async def order(turn: Turn) -> None:
        answers.append(await turn.ask("Какой кофе?"))

    add(dialog, "coffee", "сделай кофе", order)
    await dialog.submit("сделай кофе")
    for _ in range(50):
        await asyncio.sleep(0)
    assert dialog.awaiting_answer
    assert dialog.state.state == "listening"
    await dialog.submit("капучино")
    await _settle(dialog)
    assert answers == ["капучино"]


async def test_dangerous_requires_confirmation(dialog: DialogManager, speech: NullOutput) -> None:
    ran: list[bool] = []

    async def shutdown(turn: Turn) -> None:
        ran.append(True)

    add(dialog, "shutdown", "выключи компьютер", shutdown, dangerous=True)

    await dialog.submit("выключи компьютер")
    for _ in range(50):
        await asyncio.sleep(0)
    await dialog.submit("нет")
    await _settle(dialog)
    assert ran == []
    assert speech.spoken[-1] == "Отменено."

    await dialog.submit("выключи компьютер")
    for _ in range(50):
        await asyncio.sleep(0)
    await dialog.submit("да, выполняй")
    await _settle(dialog)
    assert ran == [True]


async def test_ask_timeout_returns_none(dialog: DialogManager) -> None:
    dialog.ask_timeout = 0.05
    result: list[str | None] = ["unset"]

    async def q(turn: Turn) -> None:
        result[0] = await turn.ask("?")

    add(dialog, "q", "вопрос", q)
    await dialog.submit("вопрос")
    await _settle(dialog)
    assert result == [None]


async def test_new_utterance_interrupts_running_turn(
    dialog: DialogManager, speech: NullOutput
) -> None:
    started = asyncio.Event()
    finished: list[str] = []

    async def slow(turn: Turn) -> None:
        started.set()
        await asyncio.sleep(10)
        finished.append("slow")

    async def fast(turn: Turn) -> None:
        finished.append("fast")

    add(dialog, "slow", "долгая задача", slow)
    add(dialog, "fast", "быстрая задача", fast)
    await dialog.submit("долгая задача")
    await started.wait()
    await dialog.submit("быстрая задача")
    await _settle(dialog)
    assert finished == ["fast"]


async def test_handler_error_is_reported(dialog: DialogManager, speech: NullOutput) -> None:
    async def broken(turn: Turn) -> None:
        raise RuntimeError("boom")

    add(dialog, "broken", "сломайся", broken)
    await dialog.submit("сломайся")
    await _settle(dialog)
    assert "ошибка" in speech.spoken[-1]
    assert dialog.state.state == "idle"
