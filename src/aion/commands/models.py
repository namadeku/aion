"""User commands and scenarios: the YAML format (see docs/commands-api.md, sections 2-3)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aion.commands.keys import parse_combo

Lang = Literal["ru", "en", "both"]
ConfirmMode = Literal["auto", "always", "never"]

MAX_WAIT_S = 600.0
MAX_SHELL_TIMEOUT_S = 120.0
MAX_ACTIONS = 100
MAX_TRIGGERS = 50
PLAYABLE = (".wav", ".mp3")
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @property
    def dangerous(self) -> bool:
        """Dangerous by its kind alone (``file`` becomes dangerous only if the file exists)."""
        return False

    def title(self) -> str:
        """A short Russian description for the work log and the confirmation question."""
        return str(getattr(self, "type", "действие"))


class OpenAction(_Action):
    type: Literal["open"] = "open"
    target: str = Field(min_length=1, max_length=1000)

    def title(self) -> str:
        return f"Открыть: {self.target}"


class SearchAction(_Action):
    type: Literal["search"] = "search"
    query: str = Field(min_length=1, max_length=500)
    engine: str | None = None

    def title(self) -> str:
        return f"Поиск: {self.query}"


class KeysAction(_Action):
    type: Literal["keys"] = "keys"
    combo: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _one_of(self) -> KeysAction:
        if (self.combo is None) == (self.text is None):
            raise ValueError("нужно одно из полей: combo (сочетание клавиш) или text (текст)")
        if self.combo is not None:
            parse_combo(self.combo)
        return self

    def title(self) -> str:
        return f"Клавиши: {self.combo}" if self.combo else f"Напечатать: {self.text}"


class MouseAction(_Action):
    type: Literal["mouse"] = "mouse"
    action: Literal["move", "click", "right", "double", "scroll"] = "click"
    x: int | None = Field(default=None, ge=-32768, le=32767)
    y: int | None = Field(default=None, ge=-32768, le=32767)
    amount: int | None = Field(default=None, ge=-100, le=100)

    @model_validator(mode="after")
    def _fields(self) -> MouseAction:
        if (self.x is None) != (self.y is None):
            raise ValueError("координаты x и y задаются вместе")
        if self.action == "move" and self.x is None:
            raise ValueError("для move нужны x и y")
        if self.action == "scroll" and not self.amount:
            raise ValueError("для scroll нужно amount (на сколько щелчков прокрутить)")
        return self

    def title(self) -> str:
        names = {
            "move": "Мышь: переместить",
            "click": "Мышь: щелчок",
            "right": "Мышь: правый щелчок",
            "double": "Мышь: двойной щелчок",
            "scroll": f"Мышь: прокрутка на {self.amount}",
        }
        where = f" ({self.x}, {self.y})" if self.x is not None else ""
        return names[self.action] + where


class WaitAction(_Action):
    type: Literal["wait"] = "wait"
    seconds: float = Field(gt=0, le=MAX_WAIT_S)

    def title(self) -> str:
        return f"Пауза {self.seconds:g} с"


class VolumeAction(_Action):
    type: Literal["volume"] = "volume"
    set: int | None = Field(default=None, ge=0, le=100)
    change: int | None = Field(default=None, ge=-100, le=100)
    mute: bool | None = None
    app: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _something(self) -> VolumeAction:
        if self.set is None and self.change is None and self.mute is None:
            raise ValueError("нужно set (уровень), change (изменение) или mute")
        if self.set is not None and self.change is not None:
            raise ValueError("set и change вместе не задаются")
        return self

    def title(self) -> str:
        where = f" ({self.app})" if self.app else ""
        if self.mute is not None and self.set is None and self.change is None:
            return ("Выключить звук" if self.mute else "Включить звук") + where
        if self.set is not None:
            return f"Громкость {self.set}%{where}"
        return f"Громкость {self.change:+d}%{where}"


class WindowAction(_Action):
    type: Literal["window"] = "window"
    action: Literal["minimize", "maximize", "close", "focus"]
    name: str = Field(min_length=1, max_length=200)

    @property
    def dangerous(self) -> bool:
        return self.action == "close"

    def title(self) -> str:
        verbs = {
            "minimize": "Свернуть",
            "maximize": "Развернуть",
            "close": "Закрыть",
            "focus": "Переключиться на",
        }
        return f"{verbs[self.action]} окно: {self.name}"


class MinimizeAllAction(_Action):
    type: Literal["minimize_all"] = "minimize_all"

    def title(self) -> str:
        return "Свернуть все окна"


class ShellAction(_Action):
    type: Literal["shell"] = "shell"
    command: str = Field(min_length=1, max_length=8000)
    shell: Literal["cmd", "powershell"] = "cmd"
    timeout: float | None = Field(default=None, gt=0, le=MAX_SHELL_TIMEOUT_S)

    @property
    def dangerous(self) -> bool:
        return True

    def title(self) -> str:
        name = "PowerShell" if self.shell == "powershell" else "CMD"
        return f"{name}: {self.command}"


class PowerAction(_Action):
    type: Literal["power"] = "power"
    action: Literal["shutdown", "restart", "sleep", "lock"]

    @property
    def dangerous(self) -> bool:
        return self.action != "lock"

    def title(self) -> str:
        return {
            "shutdown": "Выключить компьютер",
            "restart": "Перезагрузить компьютер",
            "sleep": "Спящий режим",
            "lock": "Заблокировать компьютер",
        }[self.action]


class DeleteAction(_Action):
    type: Literal["delete"] = "delete"
    path: str = Field(min_length=1, max_length=1000)

    @property
    def dangerous(self) -> bool:
        return True

    def title(self) -> str:
        return f"Удалить в корзину: {self.path}"


class FileAction(_Action):
    type: Literal["file"] = "file"
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(default="", max_length=1_000_000)

    def title(self) -> str:
        return f"Создать файл: {self.path}"


class DictateAction(_Action):
    type: Literal["dictate"] = "dictate"
    prompt: str | None = Field(default=None, max_length=300)

    def title(self) -> str:
        return "Диктовка"


class PlayAction(_Action):
    type: Literal["play"] = "play"
    path: str = Field(min_length=1, max_length=1000)

    @field_validator("path")
    @classmethod
    def _audio(cls, value: str) -> str:
        if Path(value).suffix.lower() not in PLAYABLE:
            raise ValueError("проигрываются только файлы .wav и .mp3")
        return value

    def title(self) -> str:
        return f"Звук: {Path(self.path).name}"


class SayAction(_Action):
    type: Literal["say"] = "say"
    text: str = Field(min_length=1, max_length=2000)

    def title(self) -> str:
        return f"Сказать: {self.text}"


class RunAction(_Action):
    type: Literal["run"] = "run"
    command: str = Field(min_length=1, max_length=200)

    def title(self) -> str:
        return f"Команда: {self.command}"


Action = Annotated[
    OpenAction
    | SearchAction
    | KeysAction
    | MouseAction
    | WaitAction
    | VolumeAction
    | WindowAction
    | MinimizeAllAction
    | ShellAction
    | PowerAction
    | DeleteAction
    | FileAction
    | DictateAction
    | PlayAction
    | SayAction
    | RunAction,
    Field(discriminator="type"),
]

ACTION_TYPES = (
    "open",
    "search",
    "keys",
    "mouse",
    "wait",
    "volume",
    "window",
    "minimize_all",
    "shell",
    "power",
    "delete",
    "file",
    "dictate",
    "play",
    "say",
    "run",
)


def check_trigger(trigger: str) -> str:
    """Check a phrase template the way the router reads it (brackets, slots)."""
    from aion.core.router import CommandSpec, Router

    async def _noop(*_: Any, **__: Any) -> None:
        return None

    text = trigger.strip()
    if not text:
        raise ValueError("пустая фраза")
    depth = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    for char in text:
        if char in depth:
            depth[char] += 1
            if depth[char] > 1:
                raise ValueError(f"вложенные скобки в «{text}»")
        elif char in pairs:
            depth[pairs[char]] -= 1
            if depth[pairs[char]] < 0:
                raise ValueError(f"лишняя закрывающая скобка в «{text}»")
    if any(depth.values()):
        raise ValueError(f"незакрытая скобка в «{text}»")
    for slot in re.findall(r"\{([^{}]*)\}", text):
        if not slot.isidentifier():
            raise ValueError(f"имя {{{slot}}} в «{text}»: только буквы, цифры и _")
    try:
        Router().set_commands([CommandSpec("check", "check", (text,), _noop)])
    except re.error as e:
        raise ValueError(f"не получается разобрать «{text}»: {e}") from e
    return text


class Command(BaseModel):
    """One command; with more than one action it is a scenario."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    triggers: list[str] = Field(default_factory=list, max_length=MAX_TRIGGERS)
    lang: Lang = "ru"
    enabled: bool = True
    confirm: ConfirmMode = "auto"
    description: str = Field(default="", max_length=1000)
    actions: list[Action] = Field(min_length=1, max_length=MAX_ACTIONS)

    @field_validator("triggers")
    @classmethod
    def _triggers(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(check_trigger(t) for t in value))

    @property
    def dangerous(self) -> bool:
        """Some action is dangerous by its kind (``run`` is judged where the target is known)."""
        return any(a.dangerous for a in self.actions)

    def to_yaml_data(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data["actions"] = [a.model_dump(mode="json", exclude_none=True) for a in self.actions]
        return data


class PackMeta(BaseModel):
    """``pack.yaml`` in the root of a command pack."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    lang: Lang = "both"


def trigger_lang(trigger: str) -> Literal["ru", "en"]:
    """The language of one phrase by its alphabet: Cyrillic is Russian, the rest English."""
    return "ru" if _CYRILLIC.search(trigger) else "en"


def triggers_for(command: Command, selected: Lang) -> list[str]:
    """Triggers to register when the user picked ``selected`` (a pack's language choice)."""
    if selected == "both":
        return list(command.triggers)
    if command.lang not in (selected, "both"):
        return []
    if command.lang == "both":
        return [t for t in command.triggers if trigger_lang(t) == selected]
    return list(command.triggers)


# -- Russian validation messages (the editor shows them as they are) ---------------------------

_FIELD_NAMES = {
    "name": "имя",
    "triggers": "фразы",
    "lang": "язык",
    "confirm": "подтверждение",
    "description": "описание",
    "actions": "действия",
    "enabled": "включена",
}


def _where(loc: tuple[int | str, ...]) -> str:
    parts: list[str] = []
    items = list(loc)
    while items:
        item = items.pop(0)
        if item == "actions" and items and isinstance(items[0], int):
            parts.append(f"действие {int(items.pop(0)) + 1}")
            if items and isinstance(items[0], str) and items[0] in ACTION_TYPES:
                items.pop(0)  # the union branch name pydantic adds
        elif item == "triggers" and items and isinstance(items[0], int):
            parts.append(f"фраза {int(items.pop(0)) + 1}")
        elif isinstance(item, str):
            parts.append(_FIELD_NAMES.get(item, item))
        else:
            parts.append(str(item))
    return ", ".join(parts)


def _num(value: Any) -> str:
    """``600.0`` -> ``600``, ``0.5`` -> ``0.5``."""
    return f"{value:g}" if isinstance(value, float) else str(value)


def _message(error: Any) -> str:  # noqa: PLR0912 - one case per pydantic error kind
    kind: str = error["type"]
    ctx: dict[str, Any] = error.get("ctx") or {}
    match kind:
        case "missing":
            return "обязательное поле"
        case "extra_forbidden":
            return "неизвестное поле"
        case "union_tag_invalid":
            return f"неизвестный тип действия «{ctx.get('tag', '')}»"
        case "union_tag_not_found":
            return "у действия нет поля type"
        case "literal_error":
            return f"допустимые значения: {ctx.get('expected', '')}"
        case "string_too_short":
            return "не может быть пустым"
        case "string_too_long":
            return f"слишком длинно (не больше {ctx.get('max_length')} символов)"
        case "too_short":
            return "нужно хотя бы одно значение"
        case "too_long":
            return f"слишком много (не больше {ctx.get('max_length')})"
        case "less_than_equal":
            return f"не больше {_num(ctx.get('le'))}"
        case "less_than":
            return f"меньше {_num(ctx.get('lt'))}"
        case "greater_than_equal":
            return f"не меньше {_num(ctx.get('ge'))}"
        case "greater_than":
            return f"больше {_num(ctx.get('gt'))}"
        case "int_type" | "int_parsing" | "int_from_float":
            return "нужно целое число"
        case "float_type" | "float_parsing":
            return "нужно число"
        case "bool_type" | "bool_parsing":
            return "нужно да/нет (true/false)"
        case "string_type":
            return "нужен текст"
        case "list_type":
            return "нужен список"
        case "model_type" | "model_attributes_type" | "dict_type":
            return "нужен набор полей"
        case "value_error" | "assertion_error":
            return str(ctx.get("error", error.get("msg", "")))
        case _:
            return str(error.get("msg", kind))


def explain(error: ValidationError) -> list[str]:
    """Readable Russian lines for a failed command validation."""
    lines: list[str] = []
    for item in error.errors(include_url=False):
        where = _where(tuple(item["loc"]))
        message = _message(item)
        lines.append(f"{where}: {message}" if where else message)
    return lines
