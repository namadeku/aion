"""User commands: the YAML model, validation messages, key combos and macro recording."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from aion.commands.keys import parse_combo
from aion.commands.models import (
    Command,
    FileAction,
    KeysAction,
    PowerAction,
    RunAction,
    WindowAction,
    explain,
    trigger_lang,
    triggers_for,
)
from aion.commands.recorder import MacroRecorder, key_name, to_actions


def command(**fields: Any) -> Command:
    data: dict[str, Any] = {"name": "Тест", "actions": [{"type": "say", "text": "привет"}]}
    return Command.model_validate(data | fields)


def errors(**fields: Any) -> list[str]:
    with pytest.raises(ValidationError) as info:
        command(**fields)
    return explain(info.value)


def test_contract_example_parses() -> None:
    cmd = command(
        name="Протокол гостей",
        triggers=["протокол гостей", "гости пришли"],
        actions=[
            {"type": "minimize_all"},
            {"type": "volume", "set": 20},
            {"type": "wait", "seconds": 0.5},
            {"type": "open", "target": "https://music.yandex.ru"},
            {"type": "say", "text": "Готово"},
        ],
    )
    assert [a.type for a in cmd.actions] == ["minimize_all", "volume", "wait", "open", "say"]
    assert cmd.lang == "ru"
    assert cmd.confirm == "auto"
    assert not cmd.dangerous
    data = cmd.to_yaml_data()
    assert data["actions"][1] == {"type": "volume", "set": 20}  # no null fields in YAML


@pytest.mark.parametrize(
    ("action", "dangerous"),
    [
        ({"type": "shell", "command": "echo 1"}, True),
        ({"type": "power", "action": "shutdown"}, True),
        ({"type": "power", "action": "lock"}, False),
        ({"type": "delete", "path": "C:/x.txt"}, True),
        ({"type": "window", "action": "close", "name": "блокнот"}, True),
        ({"type": "window", "action": "focus", "name": "блокнот"}, False),
        ({"type": "file", "path": "C:/x.txt", "content": "a"}, False),  # only if it exists
        ({"type": "keys", "combo": "ctrl+c"}, False),
    ],
)
def test_dangerous_actions(action: dict[str, Any], dangerous: bool) -> None:
    assert command(actions=[action]).dangerous is dangerous


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"actions": [{"type": "fly"}]}, "действие 1: неизвестный тип действия «fly»"),
        ({"actions": [{"type": "wait", "seconds": 601}]}, "действие 1, seconds: не больше 600"),
        ({"actions": [{"type": "shell", "command": "x", "timeout": 500}]}, "не больше 120"),
        ({"actions": [{"type": "keys"}]}, "нужно одно из полей"),
        ({"actions": [{"type": "keys", "combo": "ctrl+banana"}]}, "Неизвестная клавиша «banana»"),
        ({"actions": [{"type": "mouse", "action": "move"}]}, "для move нужны x и y"),
        ({"actions": [{"type": "volume"}]}, "нужно set"),
        ({"actions": [{"type": "play", "path": "C:/a.ogg"}]}, ".wav и .mp3"),
        ({"actions": [{"type": "say", "text": "x", "loud": True}]}, "loud: неизвестное поле"),
        ({"actions": []}, "действия: нужно хотя бы одно значение"),
        ({"name": ""}, "имя: не может быть пустым"),
        ({"triggers": ["включи (свет"]}, "фразы: незакрытая скобка в «включи (свет»"),
        ({"triggers": ["скажи {два слова}"]}, "только буквы, цифры и _"),
        ({"lang": "de"}, "язык: допустимые значения"),
    ],
)
def test_validation_errors_are_russian(fields: dict[str, Any], expected: str) -> None:
    assert any(expected in line for line in errors(**fields)), errors(**fields)


def test_triggers_are_deduplicated_and_stripped() -> None:
    cmd = command(triggers=["  привет  ", "привет", "здравствуй {name}"])
    assert cmd.triggers == ["привет", "здравствуй {name}"]


def test_action_titles_for_the_work_log() -> None:
    assert WindowAction(action="close", name="блокнот").title() == "Закрыть окно: блокнот"
    assert PowerAction(action="restart").title() == "Перезагрузить компьютер"
    assert KeysAction(combo="ctrl+c").title() == "Клавиши: ctrl+c"
    assert RunAction(command="work/morning").title() == "Команда: work/morning"
    assert FileAction(path="C:/a.txt").title() == "Создать файл: C:/a.txt"


@pytest.mark.parametrize(
    ("combo", "keys"),
    [
        ("ctrl+shift+esc", ["ctrl", "shift", "esc"]),
        ("Win + D", ["win", "d"]),
        ("ctrl++", ["ctrl", "+"]),
        ("alt+f4", ["alt", "f4"]),
        ("win+.", ["win", "."]),
    ],
)
def test_parse_combo(combo: str, keys: list[str]) -> None:
    assert parse_combo(combo) == keys


@pytest.mark.parametrize("combo", ["", "ctrl+", "ctrl+shift+alt+win+a+b", "hyper+x"])
def test_parse_combo_rejects(combo: str) -> None:
    with pytest.raises(ValueError, match="клавиш"):
        parse_combo(combo)


def test_trigger_language_selection() -> None:
    both = command(lang="both", triggers=["открой почту", "open mail"])
    assert trigger_lang("open mail") == "en"
    assert trigger_lang("открой почту") == "ru"
    assert triggers_for(both, "both") == ["открой почту", "open mail"]
    assert triggers_for(both, "ru") == ["открой почту"]
    assert triggers_for(both, "en") == ["open mail"]
    russian = command(lang="ru", triggers=["привет"])
    assert triggers_for(russian, "en") == []
    assert triggers_for(russian, "ru") == ["привет"]


# -- macro recording ---------------------------------------------------------------------------


class _Key:
    def __init__(self, char: str | None = None, name: str | None = None) -> None:
        self.char = char
        self.name = name


class _Button:
    def __init__(self, name: str) -> None:
        self.name = name


def test_key_names_from_pynput() -> None:
    assert key_name(_Key(char="A")) == "a"
    assert key_name(_Key(name="ctrl_l")) == "ctrl"
    assert key_name(_Key(name="cmd")) == "win"
    assert key_name(_Key(name="f5")) == "f5"
    assert key_name(_Key(name="page_down")) == "pagedown"
    assert key_name(_Key(name="unknown_media_key")) is None


def test_recorder_turns_input_into_actions() -> None:
    recorder = MacroRecorder()
    recorder.start(listen=False)  # no real hooks in tests: events are fed by hand
    for char in "hi":
        recorder.on_press(_Key(char=char))
    recorder.on_press(_Key(name="ctrl_l"))
    recorder.on_press(_Key(char="s"))
    recorder.on_release(_Key(name="ctrl_l"))
    recorder.on_click(10, 20, _Button("left"), True)
    recorder.on_click(10, 20, _Button("left"), False)
    recorder.on_click(10, 20, _Button("left"), True)  # the second click: a double click
    recorder.on_scroll(10, 20, 0, -2)
    actions = recorder.stop()
    assert actions == [
        {"type": "keys", "text": "hi"},
        {"type": "keys", "combo": "ctrl+s"},
        {"type": "mouse", "action": "double", "x": 10, "y": 20},
        {"type": "mouse", "action": "scroll", "x": 10, "y": 20, "amount": -2},
    ]
    command(actions=actions)  # the editor can save what was recorded
    recorder.on_press(_Key(char="x"))  # after stop nothing is recorded
    assert recorder.stop() == []


def test_recorded_pauses_become_waits() -> None:
    events = [
        (0.0, "key", {"key": "a", "char": "a"}),
        (0.1, "key", {"key": "space", "char": None}),
        (0.2, "key", {"key": "b", "char": "b"}),
        (2.0, "key", {"key": "enter", "char": None}),
        (2.1, "click", {"x": 5, "y": 6, "button": "right"}),
    ]
    assert to_actions(events) == [
        {"type": "keys", "text": "a b"},
        {"type": "wait", "seconds": 1.8},
        {"type": "keys", "combo": "enter"},
        {"type": "mouse", "action": "right", "x": 5, "y": 6},
    ]
