"""Desktop integration: native window (pywebview), tray icon, global hotkey, autostart."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "Aion"
# The installer and uninstaller wait for this mutex to disappear (installer/aion.iss).
INSTANCE_MUTEX = "AionAppMutex"
_mutex_handles: list[int] = []  # kept open until the process exits
# The desktop mascot is rendered by an off-screen window: Chromium must not pause it as
# "occluded". WebView2 reads extra flags from this variable (only one --disable-features list
# counts, so pywebview's own ElasticOverscroll is repeated here).
WEBVIEW2_ARGS = (
    "--disable-features=ElasticOverscroll,CalculateNativeWinOcclusion "
    "--disable-background-timer-throttling --disable-renderer-backgrounding "
    "--disable-backgrounding-occluded-windows"
)


def autostart_command() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return f'"{sys.executable}" run'
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" -m aion run'


def hold_instance_mutex() -> None:
    """Signal "Aion is running" to the installer for the lifetime of the process."""
    if sys.platform != "win32" or _mutex_handles:
        return
    import ctypes

    _mutex_handles.append(ctypes.windll.kernel32.CreateMutexW(None, False, INSTANCE_MUTEX))


def set_autostart(enabled: bool) -> None:
    """Start with Windows via the per-user Run registry key (no admin rights needed)."""
    if sys.platform != "win32":
        logger.warning("Автозапуск пока реализован только для Windows")
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(key, AUTOSTART_NAME)
    logger.info("Автозапуск {}", "включён" if enabled else "выключен")


class Hotkey:
    """Global push-to-talk hotkey (pynput), callback runs on the asyncio loop."""

    def __init__(self, combo: str, loop: asyncio.AbstractEventLoop, callback: Callable[[], Any]):
        self.combo = combo
        self._loop = loop
        self._callback = callback
        self._listener: Any = None

    def start(self) -> None:
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({self.combo: self._fire})
            self._listener.start()
            logger.info("Горячая клавиша «нажми и говори»: {}", self.combo)
        except Exception as e:
            logger.warning("Горячая клавиша {} недоступна: {}", self.combo, e)

    def _fire(self) -> None:
        def run() -> None:
            result = self._callback()
            if asyncio.iscoroutine(result):
                self._loop.create_task(result)

        self._loop.call_soon_threadsafe(run)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()


def ensure_icon(path: Path) -> Path:
    """Write the Aion .ico (generated, no binary in the repo) and return its path."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _tray_image(256).save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


def create_desktop_shortcut(icon: Path) -> Path:
    """Desktop shortcut that starts Aion without a console window."""
    if sys.platform != "win32":
        raise RuntimeError("Ярлык создаётся только в Windows")
    if getattr(sys, "frozen", False):
        target, arguments, workdir = sys.executable, "run", str(Path(sys.executable).parent)
    else:
        target = str(Path(sys.executable).with_name("pythonw.exe"))
        arguments, workdir = "-m aion run", str(Path.cwd())
    desktop = _desktop_dir()
    link = desktop / "Aion.lnk"
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:AION_LINK);"
        "$s.TargetPath = $env:AION_TARGET; $s.Arguments = $env:AION_ARGS;"
        "$s.WorkingDirectory = $env:AION_WORKDIR; $s.IconLocation = $env:AION_ICON;"
        "$s.Description = 'Aion — голосовой ассистент'; $s.Save()"
    )
    env = {
        **os.environ,
        "AION_LINK": str(link),
        "AION_TARGET": target,
        "AION_ARGS": arguments,
        "AION_WORKDIR": workdir,
        "AION_ICON": str(icon),
    }
    subprocess.run(  # paths go through env vars: no quoting issues with Cyrillic/spaces
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        env=env,
    )
    return link


def _desktop_dir() -> Path:
    import ctypes
    from ctypes import wintypes

    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return Path(buf.value)


def _tray_image(size: int = 64) -> Any:
    from PIL import Image, ImageDraw

    k = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2 * k, 2 * k, 62 * k, 62 * k), fill=(4, 7, 12, 255))
    d.ellipse((6 * k, 6 * k, 58 * k, 58 * k), outline=(84, 214, 255, 255), width=round(6 * k))
    d.ellipse((24 * k, 24 * k, 40 * k, 40 * k), fill=(84, 214, 255, 255))
    return img


class Tray:
    def __init__(
        self,
        title: str,
        on_open: Callable[[], None],
        on_toggle_mute: Callable[[], None],
        is_muted: Callable[[], bool],
        on_quit: Callable[[], None],
        on_toggle_desktop: Callable[[], None] | None = None,
        is_desktop: Callable[[], bool] | None = None,
    ) -> None:
        self.title = title
        self._on_open = on_open
        self._on_toggle_mute = on_toggle_mute
        self._is_muted = is_muted
        self._on_quit = on_quit
        self._on_toggle_desktop = on_toggle_desktop
        self._is_desktop = is_desktop or (lambda: False)
        self._icon: Any = None

    def start(self) -> None:
        try:
            import pystray

            items = [
                pystray.MenuItem("Открыть", self._on_open, default=True),
                pystray.MenuItem(
                    "Микрофон выключен",
                    self._on_toggle_mute,
                    checked=lambda _: self._is_muted(),
                ),
            ]
            if self._on_toggle_desktop is not None:
                items.append(
                    pystray.MenuItem(
                        "Персонаж на рабочем столе",
                        self._on_toggle_desktop,
                        checked=lambda _: self._is_desktop(),
                    )
                )
            menu = pystray.Menu(
                *items, pystray.Menu.SEPARATOR, pystray.MenuItem("Выход", self._on_quit)
            )
            self._icon = pystray.Icon("aion", _tray_image(), self.title, menu)
            self._icon.run_detached()
        except Exception as e:
            logger.warning("Иконка в трее недоступна: {}", e)

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()


class Window:
    """pywebview window; must run on the main thread (blocks until closed)."""

    def __init__(self, title: str, url: str) -> None:
        self.title = title
        self.url = url
        self._window: Any = None
        self.closed = threading.Event()

    def run(self) -> bool:
        try:
            import webview
        except ImportError:
            return False
        self._window = webview.create_window(
            self.title,
            self.url,
            width=1280,
            height=800,
            min_size=(760, 560),
            background_color="#04070C",
        )
        self._window.events.closed += self.closed.set
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", WEBVIEW2_ARGS)
        webview.start()
        return True

    def show(self) -> None:
        if self._window is not None:
            self._window.show()
            self._window.restore()

    def hide(self) -> None:
        if self._window is not None:
            self._window.hide()

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()
