"""What command actions do to the computer, behind one interface (tests swap in a fake).

Reuses the builtin plugins instead of repeating them: program/site/folder names are resolved
and windows are found by the ``apps`` plugin (through its tools, as the ``system`` plugin
already does for music apps); volume, mute, lock and power go through ``system``'s
``control`` module, the very code its commands run.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import sys
import types
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from aion.commands.keys import NAMED_KEYS
from aion.commands.shell import ShellKind, ShellResult, run_shell
from aion.plugins.manager import BUILTIN_DIR

if TYPE_CHECKING:
    from aion.app import Aion

_HELPERS = "aion_command_helpers"


class ActionError(Exception):
    """An action could not be done; the text is said to the user and shown in the log."""


class Backend(Protocol):
    async def open(self, target: str) -> str: ...
    async def search(self, query: str, engine: str | None) -> str: ...
    async def keys(self, keys: list[str]) -> None: ...
    async def type_keys(self, text: str) -> None: ...
    async def paste(self, text: str) -> str: ...
    async def mouse(
        self, action: str, x: int | None, y: int | None, amount: int | None
    ) -> None: ...
    async def volume(
        self, *, set_to: int | None, change: int | None, mute: bool | None, app: str | None
    ) -> str: ...
    async def window(self, action: str, name: str) -> str: ...
    async def minimize_all(self) -> None: ...
    async def power(self, action: str) -> None: ...
    async def recycle(self, path: Path) -> None: ...
    async def play(self, path: Path) -> None: ...
    async def shell(self, command: str, shell: ShellKind, timeout: float) -> ShellResult: ...


def builtin_module(plugin: str, module: str) -> types.ModuleType:
    """A helper module of a builtin plugin (``system/control.py``), loaded once by path.

    Builtin plugins are folders, not packages of ``aion``; the plugin manager loads them by
    path too. These helper modules have no relative imports.
    """
    name = f"{_HELPERS}.{plugin}.{module}"
    if (loaded := sys.modules.get(name)) is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, BUILTIN_DIR / plugin / f"{module}.py")
    if spec is None or spec.loader is None:
        raise ActionError(f"Нет модуля {plugin}/{module}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def expand_path(raw: str) -> Path:
    """``%USERPROFILE%\\x`` and ``~/x`` -> an absolute path (relative ones are refused)."""
    path = Path(os.path.expandvars(os.path.expanduser(raw.strip().strip('"'))))
    if not path.is_absolute():
        raise ActionError(f"Нужен полный путь: «{raw}»")
    return path


def _failed(reply: str) -> bool:
    """Replies of the apps tools that mean "nothing was done"."""
    return reply.startswith(("Не вижу", "Не найдено", "Не получилось", "Не удалось"))


class DesktopBackend:
    """The real thing (Windows first; other systems where the reused helpers support them)."""

    def __init__(self, app: Aion) -> None:
        self.app = app

    # -- apps plugin ----------------------------------------------------------------------

    async def _apps_tool(self, tool: str, **args: Any) -> str:
        spec = next((t for t in self.app.plugins.tools() if t.qualified_name == tool), None)
        if spec is None:
            raise ActionError("Нужен встроенный плагин «Программы и сайты» — он выключен")
        return await spec.invoke(args)

    async def open(self, target: str) -> str:
        launcher = builtin_module("apps", "launcher")
        text = target.strip()
        if text.startswith(("http://", "https://")):
            await asyncio.to_thread(webbrowser.open, text)
            return f"Открываю {text}"
        with contextlib.suppress(ActionError):
            path = expand_path(text)
            if path.exists():
                await asyncio.to_thread(
                    launcher.open_target, launcher.Target("folder", path.name, str(path))
                )
                return f"Открываю {path}"
        reply = await self._apps_tool("apps__open_app", name=text)
        if _failed(reply):
            raise ActionError(reply)
        return reply

    async def search(self, query: str, engine: str | None) -> str:
        launcher = builtin_module("apps", "launcher")
        default = self.app.config.plugins.settings.get("apps", {}).get("search_engine", "google")
        url = launcher.search_url(engine or str(default), query)
        await asyncio.to_thread(webbrowser.open, url)
        return url

    async def paste(self, text: str) -> str:
        reply = await self._apps_tool("apps__type_text_in", text=text, app="")
        if _failed(reply):
            raise ActionError(reply)
        return reply

    async def window(self, action: str, name: str) -> str:
        tool = {
            "minimize": "apps__minimize_app",
            "close": "apps__close_app",
            "focus": "apps__switch_to_app",
            "maximize": "apps__switch_to_app",
        }[action]
        reply = await self._apps_tool(tool, name=name)
        if _failed(reply):
            raise ActionError(reply)
        if action == "maximize":
            await asyncio.to_thread(_maximize_foreground)
            return f"Развернуто: {name}"
        return reply

    async def minimize_all(self) -> None:
        windows = builtin_module("apps", "windows")
        await asyncio.to_thread(windows.minimize_all)

    # -- keyboard and mouse (pynput) ------------------------------------------------------

    async def keys(self, keys: list[str]) -> None:
        await asyncio.to_thread(_press_combo, keys)

    async def type_keys(self, text: str) -> None:
        from pynput.keyboard import Controller

        await asyncio.to_thread(Controller().type, text)

    async def mouse(self, action: str, x: int | None, y: int | None, amount: int | None) -> None:
        await asyncio.to_thread(_mouse, action, x, y, amount)

    # -- system plugin ----------------------------------------------------------------------

    async def volume(
        self, *, set_to: int | None, change: int | None, mute: bool | None, app: str | None
    ) -> str:
        if app:
            level = await asyncio.to_thread(app_volume, app, set_to, change, mute)
            return f"{app}: {level}%"
        control = builtin_module("system", "control")
        level: int | None = None
        if set_to is not None:
            level = await asyncio.to_thread(control.set_volume, set_to)
        elif change is not None:
            level = await asyncio.to_thread(control.change_volume, abs(change), change > 0)
        if mute is not None:
            await asyncio.to_thread(control.set_mute, mute)
        if level is not None:
            return f"Громкость {level}%"
        return "Звук выключен" if mute else "Звук включён"

    async def power(self, action: str) -> None:
        control = builtin_module("system", "control")
        if action == "lock":
            await asyncio.to_thread(control.lock_screen)
        else:
            await asyncio.to_thread(control.power, action)

    # -- files, sound, shell ----------------------------------------------------------------

    async def recycle(self, path: Path) -> None:
        await asyncio.to_thread(move_to_recycle_bin, path)

    async def play(self, path: Path) -> None:
        player = getattr(self.app.speech, "player", None)
        if player is None:
            raise ActionError("Звук недоступен: голосовой вывод выключен")
        samples, rate = await asyncio.to_thread(decode_audio, path)
        await player.play(samples, rate)

    async def shell(self, command: str, shell: ShellKind, timeout: float) -> ShellResult:
        return await run_shell(command, shell, timeout)


# -- helpers (blocking: called in worker threads) ---------------------------------------------


def _key(name: str) -> Any:
    from pynput.keyboard import Key, KeyCode

    if name in NAMED_KEYS:
        return getattr(Key, NAMED_KEYS[name])
    return KeyCode.from_char(name)


def _press_combo(keys: list[str]) -> None:
    from pynput.keyboard import Controller

    keyboard = Controller()
    pressed: list[Any] = []
    try:
        for name in keys:
            key = _key(name)
            keyboard.press(key)
            pressed.append(key)
    finally:
        for key in reversed(pressed):
            keyboard.release(key)


def _mouse(action: str, x: int | None, y: int | None, amount: int | None) -> None:
    from pynput.mouse import Button, Controller

    mouse = Controller()
    if x is not None and y is not None:
        mouse.position = (x, y)
    match action:
        case "click":
            mouse.click(Button.left, 1)
        case "right":
            mouse.click(Button.right, 1)
        case "double":
            mouse.click(Button.left, 2)
        case "scroll":
            mouse.scroll(0, amount or 0)
        case _:
            pass  # "move": done above


def _maximize_foreground() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.ShowWindow(user32.GetForegroundWindow(), 3)  # SW_MAXIMIZE


def _process_matches(process: str, app: str) -> bool:
    name = process.lower().removesuffix(".exe")
    wanted = app.lower().strip().removesuffix(".exe")
    return bool(wanted) and (name == wanted or wanted in name)


def app_volume(app: str, set_to: int | None, change: int | None, mute: bool | None) -> int:
    """Volume of one program's audio sessions (Windows mixer), by its process name."""
    if sys.platform != "win32":
        raise ActionError("Громкость отдельной программы есть только в Windows")
    import comtypes  # pyright: ignore[reportMissingTypeStubs]
    from pycaw.pycaw import AudioUtilities  # pyright: ignore[reportMissingTypeStubs]

    with contextlib.suppress(OSError):
        comtypes.CoInitialize()
    sessions = [
        s
        for s in AudioUtilities.GetAllSessions()  # pyright: ignore[reportUnknownMemberType]
        if s.Process is not None and _process_matches(s.Process.name(), app)
    ]
    if not sessions:
        raise ActionError(f"Программа «{app}» сейчас не выводит звук")
    level = 0
    for session in sessions:
        volume: Any = session.SimpleAudioVolume
        current = round(float(volume.GetMasterVolume()) * 100)
        level = current
        if set_to is not None:
            level = set_to
        elif change is not None:
            level = current + change
        level = max(0, min(100, level))
        volume.SetMasterVolume(level / 100, None)
        if mute is not None:
            volume.SetMute(int(mute), None)
    return level


def decode_audio(path: Path) -> tuple[Any, int]:
    import miniaudio
    import numpy as np

    if not path.is_file():
        raise ActionError(f"Нет файла {path}")
    try:
        decoded = miniaudio.decode_file(
            str(path), output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1
        )
    except miniaudio.DecodeError as e:
        raise ActionError(f"Не получается прочитать звук {path.name}") from e
    return np.frombuffer(decoded.samples, dtype=np.float32).copy(), decoded.sample_rate


def move_to_recycle_bin(path: Path) -> None:
    """Delete to the Recycle Bin (undoable) with the shell API — never a permanent delete."""
    if sys.platform != "win32":
        raise ActionError("Удаление в корзину есть только в Windows")
    import ctypes
    from ctypes import wintypes

    class _FileOp(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    fo_delete = 3
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    buffer = ctypes.create_unicode_buffer(str(path) + "\0")  # the list ends with two NULs
    op = _FileOp()
    op.wFunc = fo_delete
    op.pFrom = ctypes.cast(buffer, ctypes.c_wchar_p)
    op.fFlags = flags
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
    if result != 0 or op.fAnyOperationsAborted:
        raise ActionError(f"Не удалось удалить {path} (код {result})")
