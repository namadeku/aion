"""The command engine: router registration, prefix mode, actions, confirmation, cancellation.

Nothing here touches the computer: actions go to ``FakeBackend``. The only real processes
are harmless ``echo``/``ping`` shells in the shell runner tests.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from aion.app import Aion
from aion.commands.engine import CommandEngine, check_deletable
from aion.commands.models import Command
from aion.commands.platform import ActionError
from aion.commands.shell import ShellResult, ShellTimeout, run_shell
from aion.commands.store import CommandStore
from aion.core import NullOutput
from aion.core.events import (
    ConfirmRequested,
    Event,
    RunFinished,
    RunStarted,
    StepFinished,
    StepStarted,
)
from aion.llm.base import Message, StreamEvent, TextDelta, ToolCall, ToolDef, TurnEnd
from tests.helpers import say, settle


class FakeBackend:
    """Records what would have been done."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.shell_result = ShellResult(0, "готово")
        self.fail_open = False

    async def open(self, target: str) -> str:
        if self.fail_open:
            raise ActionError(f"Не найдено: «{target}»")
        self.calls.append(("open", target))
        return f"Открываю {target}"

    async def search(self, query: str, engine: str | None) -> str:
        self.calls.append(("search", query, engine))
        return "url"

    async def keys(self, keys: list[str]) -> None:
        self.calls.append(("keys", keys))

    async def type_keys(self, text: str) -> None:
        self.calls.append(("type_keys", text))

    async def paste(self, text: str) -> str:
        self.calls.append(("paste", text))
        return f"Напечатала: {text}"

    async def mouse(self, action: str, x: int | None, y: int | None, amount: int | None) -> None:
        self.calls.append(("mouse", action, x, y, amount))

    async def volume(
        self, *, set_to: int | None, change: int | None, mute: bool | None, app: str | None
    ) -> str:
        self.calls.append(("volume", set_to, change, mute, app))
        return "Громкость"

    async def window(self, action: str, name: str) -> str:
        self.calls.append(("window", action, name))
        return "ok"

    async def minimize_all(self) -> None:
        self.calls.append(("minimize_all",))

    async def power(self, action: str) -> None:
        self.calls.append(("power", action))

    async def recycle(self, path: Path) -> None:
        self.calls.append(("recycle", path))

    async def play(self, path: Path) -> None:
        self.calls.append(("play", path))

    async def shell(self, command: str, shell: str, timeout: float) -> ShellResult:
        self.calls.append(("shell", command, shell, timeout))
        return self.shell_result

    def kinds(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def backend(app: Aion) -> FakeBackend:
    fake = FakeBackend()
    app.commands.backend = fake
    return fake


@pytest.fixture
def events(app: Aion) -> list[Event]:
    seen: list[Event] = []
    app.bus.subscribe("*", seen.append)
    return seen


def add(app: Aion, cid: str, **fields: Any) -> Command:
    data: dict[str, Any] = {"name": cid, "triggers": [], "actions": [{"type": "say", "text": "ок"}]}
    command = Command.model_validate(data | fields)
    app.commands.store.save(cid, command)
    app.commands.register()
    return command


async def wait_for[E: Event](
    events: list[Event], kind: type[E], timeout: float = 5.0, **match: Any
) -> E:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in events:
            if isinstance(event, kind) and all(getattr(event, k) == v for k, v in match.items()):
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind.__name__} {match}")


async def asking(app: Aion) -> None:
    """Wait until the spoken question listens for the answer (it is said first)."""
    for _ in range(500):
        if app.dialog.awaiting_answer:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no question asked")


def of[E: Event](events: list[Event], kind: type[E]) -> list[E]:
    return [e for e in events if isinstance(e, kind)]


# -- registration -------------------------------------------------------------------------


async def test_examples_are_registered_as_a_virtual_plugin(app: Aion) -> None:
    match = app.router.match("протокол гостей")
    assert match is not None
    assert match.command.plugin == "commands"
    assert match.command.name == "examples/guests"
    assert app.router.match("гости пришли") is not None


async def test_disabled_and_switched_off_commands_are_not_registered(app: Aion) -> None:
    add(app, "lamp", triggers=["зажги волшебную лампу"])
    assert app.router.match("зажги волшебную лампу") is not None
    app.commands.store.toggle("lamp", False)
    app.commands.register()
    assert app.router.match("зажги волшебную лампу") is None
    app.commands.store.toggle("lamp", True)
    app.store.update({"commands": {"enabled": False}})
    await settle(app)
    assert app.router.match("зажги волшебную лампу") is None
    app.store.update({"commands": {"enabled": True}})
    await settle(app)
    assert app.router.match("зажги волшебную лампу") is not None


async def test_plugin_reload_keeps_user_commands(app: Aion) -> None:
    add(app, "lamp", triggers=["зажги волшебную лампу"])
    await app.plugins.reload("clock")  # the plugin manager re-sets its own group only
    assert app.router.match("зажги волшебную лампу") is not None
    assert app.router.match("который час") is not None


async def test_user_command_wins_an_equal_score(app: Aion, backend: FakeBackend) -> None:
    add(app, "mine", triggers=["который час"], actions=[{"type": "say", "text": "Время пить чай"}])
    assert await say(app, "который час") == ["Время пить чай"]


async def test_exact_builtin_beats_a_looser_user_match(app: Aion) -> None:
    add(app, "hour", triggers=["час"])
    match = app.router.match("который час")
    assert match is not None
    assert match.command.plugin == "clock"


async def test_slots_fill_action_fields(app: Aion, backend: FakeBackend) -> None:
    add(
        app,
        "video",
        triggers=["найди видео про {topic}"],
        actions=[
            {"type": "search", "query": "{topic} youtube"},
            {"type": "say", "text": "Ищу {topic}"},
        ],
    )
    assert await say(app, "найди видео про котов") == ["Ищу котов"]
    assert backend.calls == [("search", "котов youtube", None)]


# -- prefix mode --------------------------------------------------------------------------


async def test_prefix_mode_needs_the_name_for_voice(app: Aion, backend: FakeBackend) -> None:
    add(app, "lamp", triggers=["зажги волшебную лампу"], actions=[{"type": "minimize_all"}])
    app.store.update({"commands": {"require_wake_name": True}})

    await app.dialog.submit("зажги волшебную лампу", source="voice", named=False)
    await settle(app)
    assert backend.calls == []
    assert isinstance(app.speech, NullOutput)
    assert app.speech.spoken[-1] == "Я пока не знаю такой команды."  # declined: no LLM here

    await app.dialog.submit("зажги волшебную лампу", source="voice", named=True)
    await settle(app)
    assert backend.kinds() == ["minimize_all"]

    await say(app, "зажги волшебную лампу")  # typed text always works
    assert backend.kinds() == ["minimize_all", "minimize_all"]

    app.store.update({"commands": {"require_wake_name": False}})
    await app.dialog.submit("зажги волшебную лампу", source="voice", named=False)
    await settle(app)
    assert backend.kinds() == ["minimize_all"] * 3


# -- running and the work log ---------------------------------------------------------------


async def test_scenario_runs_its_steps_and_reports_them(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    spoken = await say(app, "протокол гостей")
    assert spoken == ["Готово, можно встречать гостей."]
    assert backend.calls == [
        ("minimize_all",),
        ("volume", 20, None, None, None),
        ("open", "https://music.yandex.ru"),
    ]
    await asyncio.sleep(0.05)
    run = of(events, RunStarted)[-1]
    assert run.title == "протокол гостей"
    assert run.source == "text"
    started = [e for e in of(events, StepStarted) if e.run_id == run.run_id]
    finished = [e for e in of(events, StepFinished) if e.run_id == run.run_id]
    assert [s.kind for s in started] == ["action"] * 5
    assert started[0].title == "Свернуть все окна"
    assert [f.status for f in finished] == ["ok"] * 5
    assert {f.step_id for f in finished} == {s.step_id for s in started}
    assert of(events, RunFinished)[-1].run_id == run.run_id
    assert of(events, RunFinished)[-1].status == "done"


async def test_every_action_type_reaches_the_backend(
    app: Aion, backend: FakeBackend, tmp_path: Path
) -> None:
    sound = tmp_path / "ding.wav"
    add(
        app,
        "all",
        triggers=["проверь все действия"],
        confirm="never",
        actions=[
            {"type": "open", "target": "блокнот"},
            {"type": "keys", "combo": "ctrl+shift+esc"},
            {"type": "keys", "text": "abc"},
            {"type": "mouse", "action": "click", "x": 1, "y": 2},
            {"type": "volume", "change": -10, "app": "spotify"},
            {"type": "window", "action": "maximize", "name": "блокнот"},
            {"type": "power", "action": "lock"},
            {"type": "play", "path": str(sound)},
            {"type": "shell", "command": "echo hi", "shell": "powershell", "timeout": 5},
            {"type": "power", "action": "shutdown"},
        ],
    )
    await say(app, "проверь все действия")
    assert backend.calls == [
        ("open", "блокнот"),
        ("keys", ["ctrl", "shift", "esc"]),
        ("type_keys", "abc"),
        ("mouse", "click", 1, 2, None),
        ("volume", None, -10, None, "spotify"),
        ("window", "maximize", "блокнот"),
        ("power", "lock"),
        ("play", sound),
        ("shell", "echo hi", "powershell", 5),
        ("power", "shutdown"),
    ]


async def test_shell_uses_the_configured_timeout_and_reports_failure(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    add(
        app,
        "sh",
        triggers=["запусти мой скрипт"],
        confirm="never",
        actions=[{"type": "shell", "command": "x"}],
    )
    backend.shell_result = ShellResult(2, "файл не найден")
    spoken = await say(app, "запусти мой скрипт")
    assert backend.calls == [("shell", "x", "cmd", app.config.commands.shell_timeout)]
    assert spoken == ["Не получилось: код выхода 2. файл не найден"]
    await asyncio.sleep(0.05)
    assert of(events, StepFinished)[-1].status == "error"
    assert of(events, RunFinished)[-1].status == "error"


async def test_failed_action_stops_the_scenario(app: Aion, backend: FakeBackend) -> None:
    add(
        app,
        "two",
        triggers=["открой волшебную штуку"],
        actions=[{"type": "open", "target": "штука"}, {"type": "minimize_all"}],
    )
    backend.fail_open = True
    assert await say(app, "открой волшебную штуку") == ["Не получилось: Не найдено: «штука»"]
    assert backend.calls == []


async def test_nested_commands_cycles_and_depth(app: Aion, backend: FakeBackend) -> None:
    add(app, "inner", actions=[{"type": "minimize_all"}])
    add(app, "outer", triggers=["внешняя команда"], actions=[{"type": "run", "command": "inner"}])
    await say(app, "внешняя команда")
    assert backend.kinds() == ["minimize_all"]

    add(app, "ping", triggers=["пинг понг"], actions=[{"type": "run", "command": "pong"}])
    add(app, "pong", actions=[{"type": "run", "command": "ping"}])
    assert await say(app, "пинг понг") == ["Не получилось: команда «ping» вызывает саму себя"]

    for n in range(6):
        add(app, f"level{n}", actions=[{"type": "run", "command": f"level{n + 1}"}])
    add(app, "level6", actions=[{"type": "minimize_all"}])
    add(app, "deep", triggers=["глубокая команда"], actions=[{"type": "run", "command": "level0"}])
    spoken = await say(app, "глубокая команда")
    assert spoken == ["Не получилось: слишком глубокая вложенность команд (больше 5)"]
    assert backend.kinds() == ["minimize_all"]

    add(app, "dangerous", actions=[{"type": "power", "action": "restart"}])
    add(app, "wrapper", actions=[{"type": "run", "command": "dangerous"}])
    store = app.commands.store
    assert app.commands.is_dangerous(store.entries["wrapper"])  # judged through the nesting


# -- confirmation ---------------------------------------------------------------------------


async def test_dangerous_step_confirmed_by_voice(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    add(
        app,
        "sh",
        name="Скрипт",
        triggers=["запусти мой скрипт"],
        actions=[{"type": "shell", "command": "echo 1"}],
    )
    await app.dialog.submit("запусти мой скрипт")
    asked = await wait_for(events, ConfirmRequested)
    assert asked.question == "Выполнить «Скрипт»: CMD: echo 1?"
    assert asked.timeout == app.config.commands.confirm_timeout
    await asking(app)
    await app.dialog.submit("да")
    await settle(app)
    assert backend.kinds() == ["shell"]
    await asyncio.sleep(0.05)
    confirm = next(e for e in of(events, StepStarted) if e.kind == "confirm")
    assert confirm.step_id == asked.step_id
    done = next(e for e in of(events, StepFinished) if e.step_id == asked.step_id)
    assert (done.status, done.detail) == ("ok", "Да")


async def test_dangerous_step_refused_by_voice(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    add(app, "off", triggers=["выруби всё"], actions=[{"type": "power", "action": "shutdown"}])
    await app.dialog.submit("выруби всё")
    await wait_for(events, ConfirmRequested)
    await asking(app)
    await app.dialog.submit("нет")
    await settle(app)
    assert backend.calls == []
    assert isinstance(app.speech, NullOutput)
    assert app.speech.spoken[-1] == "Отменено."
    await asyncio.sleep(0.05)
    statuses = [e.status for e in of(events, StepFinished)]
    assert statuses == ["cancelled", "cancelled"]  # the question and the action


async def test_confirmed_from_the_ui(app: Aion, backend: FakeBackend, events: list[Event]) -> None:
    add(app, "del", triggers=["удали мусор"], actions=[{"type": "shell", "command": "x"}])
    await app.dialog.submit("удали мусор")
    asked = await wait_for(events, ConfirmRequested)
    assert not app.commands.answer(asked.run_id, "other-step", True)
    assert app.commands.answer(asked.run_id, asked.step_id, True)
    await settle(app)
    assert backend.kinds() == ["shell"]
    assert not app.commands.answer(asked.run_id, asked.step_id, True)  # too late now
    assert not app.dialog.awaiting_answer  # the voice question was withdrawn


async def test_refused_from_the_ui(app: Aion, backend: FakeBackend, events: list[Event]) -> None:
    add(app, "del", triggers=["удали мусор"], actions=[{"type": "shell", "command": "x"}])
    await app.dialog.submit("удали мусор")
    asked = await wait_for(events, ConfirmRequested)
    app.commands.answer(asked.run_id, asked.step_id, False)
    await settle(app)
    assert backend.calls == []


async def test_no_answer_until_the_timeout_cancels(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    app.store.update({"commands": {"confirm_timeout": 3}})
    add(app, "del", triggers=["удали мусор"], actions=[{"type": "shell", "command": "x"}])
    started = time.monotonic()
    await app.dialog.submit("удали мусор")
    await wait_for(events, ConfirmRequested)
    await settle(app)
    assert 2.5 < time.monotonic() - started < 6
    assert backend.calls == []
    await asyncio.sleep(0.05)
    confirm_step = next(e for e in of(events, StepStarted) if e.kind == "confirm")
    done = next(e for e in of(events, StepFinished) if e.step_id == confirm_step.step_id)
    assert (done.status, done.detail) == ("cancelled", "Нет ответа")


async def test_confirm_modes(app: Aion, backend: FakeBackend, events: list[Event]) -> None:
    add(
        app,
        "never",
        triggers=["режим без вопросов"],
        confirm="never",
        actions=[{"type": "power", "action": "sleep"}],
    )
    await say(app, "режим без вопросов")
    assert backend.kinds() == ["power"]
    assert of(events, ConfirmRequested) == []

    add(
        app,
        "always",
        triggers=["режим с вопросом"],
        confirm="always",
        actions=[{"type": "minimize_all"}],
    )
    await app.dialog.submit("режим с вопросом")
    asked = await wait_for(events, ConfirmRequested)
    assert asked.question == "Выполнить «always»?"
    app.commands.answer(asked.run_id, asked.step_id, True)
    await settle(app)
    assert backend.kinds() == ["power", "minimize_all"]


async def test_overwriting_a_file_asks_first(
    app: Aion, backend: FakeBackend, events: list[Event], tmp_path: Path
) -> None:
    target = tmp_path / "notes" / "todo.txt"
    add(
        app,
        "file",
        triggers=["создай мой список"],
        actions=[{"type": "file", "path": str(target), "content": "купить хлеб"}],
    )
    await say(app, "создай мой список")  # a new file: no question
    assert target.read_text(encoding="utf-8") == "купить хлеб"
    assert of(events, ConfirmRequested) == []

    target.write_text("важное", encoding="utf-8")
    await app.dialog.submit("создай мой список")
    asked = await wait_for(events, ConfirmRequested)
    app.commands.answer(asked.run_id, asked.step_id, False)
    await settle(app)
    assert target.read_text(encoding="utf-8") == "важное"


async def test_delete_goes_to_the_recycle_bin_after_a_yes(
    app: Aion, backend: FakeBackend, events: list[Event], tmp_path: Path
) -> None:
    victim = tmp_path / "old.log"
    victim.write_text("x", encoding="utf-8")
    add(
        app,
        "clean",
        triggers=["почисти старый лог"],
        actions=[{"type": "delete", "path": str(victim)}],
    )
    await app.dialog.submit("почисти старый лог")
    asked = await wait_for(events, ConfirmRequested)
    assert "Удалить в корзину" in asked.question
    app.commands.answer(asked.run_id, asked.step_id, True)
    await settle(app)
    assert backend.calls == [("recycle", victim)]  # the fake: nothing was really deleted
    assert victim.exists()


def test_deletion_guard(tmp_path: Path) -> None:
    with pytest.raises(ActionError, match="нет такого"):
        check_deletable(tmp_path / "missing")
    with pytest.raises(ActionError, match="нельзя"):
        check_deletable(Path(tmp_path.anchor))
    with pytest.raises(ActionError, match="нельзя"):
        check_deletable(Path.home())
    if sys.platform == "win32":
        with pytest.raises(ActionError, match="системная"):
            check_deletable(Path(r"C:\Windows\System32"))
    check_deletable(tmp_path)  # an ordinary folder is fine


async def test_dictate_asks_and_pastes(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    add(
        app,
        "dict",
        triggers=["режим диктовки"],
        actions=[{"type": "dictate", "prompt": "Говорите"}],
    )
    await app.dialog.submit("режим диктовки")
    for _ in range(200):
        if app.dialog.awaiting_answer:
            break
        await asyncio.sleep(0.01)
    await app.dialog.submit("привет мир")
    await settle(app)
    assert backend.calls == [("paste", "привет мир")]


# -- cancellation ---------------------------------------------------------------------------


async def test_interrupt_cancels_a_running_scenario(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    add(app, "slow", actions=[{"type": "wait", "seconds": 60}, {"type": "minimize_all"}])
    run_id = await app.commands.start_test("slow")
    started = await wait_for(events, StepStarted, run_id=run_id)
    assert started.title == "Пауза 60 с"
    await app.dialog.interrupt("hotkey")  # the "Стоп" button
    await asyncio.sleep(0.05)
    finished = await wait_for(events, RunFinished, run_id=run_id)
    assert finished.status == "interrupted"
    step = await wait_for(events, StepFinished, step_id=started.step_id)
    assert step.status == "cancelled"
    assert backend.calls == []


async def test_ui_test_run_is_its_own_run(
    app: Aion, backend: FakeBackend, events: list[Event]
) -> None:
    run_id = await app.commands.start_test("examples/guests")
    await settle(app)
    await asyncio.sleep(0.05)
    run = next(e for e in of(events, RunStarted) if e.run_id == run_id)
    assert (run.title, run.source) == ("Протокол гостей", "ui")
    assert (await wait_for(events, RunFinished, run_id=run_id)).status == "done"
    assert backend.kinds() == ["minimize_all", "volume", "open"]
    with pytest.raises(KeyError):
        await app.commands.start_test("missing")


# -- hot reload -------------------------------------------------------------------------------


async def test_folder_changes_are_picked_up(app: Aion, tmp_path: Path) -> None:
    store = CommandStore(tmp_path / "watched", examples=None)
    engine = CommandEngine(app, store=store)
    await engine.start(watch=True)
    try:
        await asyncio.sleep(0.3)  # the watcher is up
        (store.root / "lamp.yaml").write_text(
            "name: Лампа\ntriggers: [волшебная лампа сияй]\nactions: [{type: minimize_all}]\n",
            encoding="utf-8",
        )
        for _ in range(100):
            if app.router.match("волшебная лампа сияй") is not None:
                break
            await asyncio.sleep(0.1)
        match = app.router.match("волшебная лампа сияй")
        assert match is not None
        assert match.command.name == "lamp"
    finally:
        await engine.stop()


# -- the shell runner (real but harmless processes) ------------------------------------------


async def test_shell_echo() -> None:
    result = await run_shell("echo hello", "cmd", 20)
    assert result == ShellResult(0, "hello")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
async def test_powershell_utf8_output() -> None:
    result = await run_shell("Write-Output 'привет'", "powershell", 60)
    assert (result.code, result.output) == (0, "привет")


async def test_shell_exit_code() -> None:
    result = await run_shell("exit 3", "cmd", 20)
    assert result.code == 3


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe")
async def test_shell_timeout_kills_the_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "late.txt"
    started = time.monotonic()
    with pytest.raises(ShellTimeout):
        await run_shell(f'ping -n 4 127.0.0.1 >nul & echo late > "{marker}"', "cmd", 0.5)
    assert time.monotonic() - started < 3
    await asyncio.sleep(4)
    assert not marker.exists()  # cmd was killed with its child, so it never got to echo


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe")
async def test_cancelling_a_shell_kills_it(tmp_path: Path) -> None:
    marker = tmp_path / "late.txt"
    task = asyncio.create_task(
        run_shell(f'ping -n 4 127.0.0.1 >nul & echo late > "{marker}"', "cmd", 30)
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(4)
    assert not marker.exists()


# -- the LLM brain reports its tool calls ------------------------------------------------------


async def test_brain_tool_calls_are_steps_of_the_turn(app: Aion, events: list[Event]) -> None:
    from aion.llm.base import LlmProvider
    from aion.llm.brain import Brain

    class Provider(LlmProvider):
        name = "fake"

        async def stream(  # type: ignore[override]
            self, messages: list[Message], *, system: str, tools: list[ToolDef], **_: Any
        ) -> Any:
            if messages[-1].role == "tool":
                for event in (
                    TextDelta("Получается 391."),
                    TurnEnd(Message("assistant", "Получается 391.")),
                ):
                    yield event
                return
            call = ToolCall("c1", "calc__calculate", {"expression": "17 * 23"})
            event: StreamEvent = TurnEnd(Message("assistant", "", tool_calls=[call]))
            yield event

    provider = Provider()
    app.llm = provider
    app.brain = Brain(app, provider)
    app.dialog.fallback = app.brain.respond
    assert await say(app, "умножь в уме семнадцать на двадцать три") == ["Получается 391."]
    await asyncio.sleep(0.05)
    run = of(events, RunStarted)[-1]
    step = next(e for e in of(events, StepStarted) if e.kind == "tool")
    assert step.run_id == run.run_id
    assert step.title == "Инструмент calc.calculate"
    assert "17 * 23" in step.detail
    done = next(e for e in of(events, StepFinished) if e.step_id == step.step_id)
    assert (done.status, done.detail) == ("ok", "391")
    assert of(events, RunFinished)[-1].run_id == run.run_id
