"""``aion plugin new <name>`` template."""

from __future__ import annotations

import re
from pathlib import Path

_MANIFEST = """\
name: {name}
title: {title}
version: 0.1.0
author: ""
description: Описание плагина {title}
# dependencies: [requests>=2.31]      # pip-пакеты, ставятся в отдельную папку
# permissions: [network]              # network, system, shell, files, clipboard, notifications
settings:
  greeting:
    type: string
    title: Приветствие
    default: Привет
"""

_MAIN = '''\
"""{title} plugin for Aion."""

from typing import Annotated

from aion.sdk import Context, Plugin, command, tool


class {cls}(Plugin):
    async def on_load(self) -> None:
        self.log.info("{title} загружен")

    @command(["{phrase}", "{phrase} {{who}}"])
    async def hello(self, ctx: Context, who: str = "") -> None:
        """Поздороваться."""
        greeting = ctx.config["greeting"]
        await ctx.say(f"{{greeting}}, {{who or ctx.user_address}}!")

    @tool("Поздороваться с человеком по имени")
    async def greet(self, name: Annotated[str, "Имя человека"]) -> str:
        return f"{{self.config['greeting']}}, {{name}}!"
'''


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) or "MyPlugin"


def create_plugin(name: str, root: Path) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Имя плагина: латиница в нижнем регистре, цифры и _, например my_plugin")
    path = root / name
    if path.exists():
        raise FileExistsError(f"{path} уже существует")
    path.mkdir(parents=True)
    title = name.replace("_", " ").capitalize()
    (path / "plugin.yaml").write_text(_MANIFEST.format(name=name, title=title), encoding="utf-8")
    (path / "main.py").write_text(
        _MAIN.format(title=title, cls=_class_name(name), phrase=f"тест {title.lower()}"),
        encoding="utf-8",
    )
    return path
