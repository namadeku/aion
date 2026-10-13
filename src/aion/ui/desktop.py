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


def webview2_installed() -> bool:
    """Whether the Edge WebView2 runtime is present (pywebview's own check, per-user or machine).

    Without it pywebview falls back to Internet Explorer, which cannot show the interface.
    """
    if sys.platform != "win32":
        return True
    import winreg

    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, path in (
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{client}"),
    ):
        try:
            with winreg.OpenKey(root, path) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0])
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return True
    return False


# shown while the core starts: on a fresh machine loading the plugins and the UI server takes
# a while, and an empty screen looks like the app did not start. The window is frameless: the
# whole page is a drag region so it can be moved before the interface arrives.
LOADING_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;height:100%;background:#16181B;color:#9BA2AB;font:14px "Segoe UI",sans-serif}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;
cursor:default;user-select:none}
svg{width:56px;height:56px;color:#38BDF0;animation:spin 2.4s linear infinite}
h1{margin:0 -.5em 0 0;font-weight:300;font-size:26px;letter-spacing:.5em;color:#E7E9EC}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){svg{animation:none}}
</style></head><body class="pywebview-drag-region"><svg viewBox="0 0 30 30" aria-hidden="true">
<path d="M24.66 12.41A10 10 0 1 1 16.74 5.15" fill="none" stroke="currentColor"
stroke-width="2.6" stroke-linecap="round"/><circle cx="21.76" cy="7.63" r="2.1"
fill="currentColor"/><circle cx="15" cy="15" r="3.4" fill="currentColor" opacity=".55"/></svg>
<h1>AION</h1><div>Запуск…</div></body></html>"""

# The frameless window moves and resizes by itself: while the left button is held, a small
# thread follows the cursor. (The system move/size loop does not start reliably from a click
# inside WebView2: the browser process holds the mouse capture.)
_MIN_SIZE = (760, 560)
_RESIZE_EDGES = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}
_SWP_NOZORDER, _SWP_NOACTIVATE, _SWP_FRAMECHANGED = 0x0004, 0x0010, 0x0020
_VK_LBUTTON = 0x01
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND, _DWMWCP_ROUND = 1, 2


class WindowControls:
    """Window buttons of the frameless window, called by the page as ``pywebview.api.*``.

    pywebview exposes public methods only; the owner is kept in a private attribute.
    """

    def __init__(self, owner: Window) -> None:
        self._owner = owner

    def minimize(self) -> None:
        self._owner.minimize()

    def toggle_maximize(self) -> bool:
        return self._owner.toggle_maximize()

    def close(self) -> None:
        self._owner.close()

    def start_drag(self) -> None:
        self._owner.track_mouse(None)

    def start_resize(self, edge: str) -> None:
        if edge in _RESIZE_EDGES:
            self._owner.track_mouse(edge)


class Window:
    """Frameless pywebview window; must run on the main thread (blocks until closed).

    It opens with :data:`LOADING_HTML`; :meth:`navigate` switches it to the interface. The page
    draws its own title bar, window buttons (:class:`WindowControls`) and resize edges; moving
    and resizing follow the cursor from Python (:meth:`track_mouse`). Maximizing fills the
    monitor's work area (a maximized frameless WinForms window would cover the taskbar).
    """

    def __init__(self, title: str) -> None:
        self.title = title
        self._window: Any = None
        self.closed = threading.Event()
        # the window rect before maximizing; None while not maximized
        self._restore_rect: tuple[int, int, int, int] | None = None
        self._tracking = False

    def run(self, on_start: Callable[[], None] | None = None) -> bool:
        """Show the window; ``on_start`` runs in a background thread once it is up."""
        try:
            import webview
        except ImportError:
            return False
        self._window = webview.create_window(
            self.title,
            html=LOADING_HTML,
            width=1280,
            height=800,
            min_size=_MIN_SIZE,
            background_color="#16181B",
            frameless=True,
            easy_drag=False,
            resizable=True,
            shadow=True,  # DWM shadow and rounded corners on Windows 11
            js_api=WindowControls(self),
        )
        events = self._window.events
        events.closed += self.closed.set
        events.shown += lambda: self._round_corners(True)
        events.minimized += lambda: self._push_state("minimized")
        events.restored += self._push_current_state
        events.loaded += self._push_current_state
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", WEBVIEW2_ARGS)
        webview.start(on_start)
        return True

    # -- native helpers -------------------------------------------------------------------

    def _hwnd(self) -> int | None:
        native = getattr(self._window, "native", None)
        if sys.platform != "win32" or native is None:
            return None
        try:
            return int(native.Handle.ToInt64())
        except Exception:
            return None

    def track_mouse(self, edge: str | None) -> None:
        """Move (``edge`` None) or resize the window while the left button stays pressed."""
        hwnd = self._hwnd()
        if hwnd is None or self._tracking:
            return
        if edge is not None and self._restore_rect is not None:
            return  # a maximized window is not resized by its edges
        self._tracking = True
        threading.Thread(
            target=self._track_loop, args=(hwnd, edge), name="aion-window-track", daemon=True
        ).start()

    def _track_loop(self, hwnd: int, edge: str | None) -> None:
        import ctypes
        import time
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        flags = _SWP_NOZORDER | _SWP_NOACTIVATE
        try:
            cursor = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            x0, y0 = cursor.x, cursor.y
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
            dpi = user32.GetDpiForWindow(hwnd) or 96
            min_w, min_h = (round(v * dpi / 96) for v in _MIN_SIZE)
            last = (left, top, right, bottom)
            while user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000:
                user32.GetCursorPos(ctypes.byref(cursor))
                dx, dy = cursor.x - x0, cursor.y - y0
                if edge is None and self._restore_rect is not None:
                    if abs(dx) + abs(dy) < 8:  # a click (or double click) is not a drag
                        time.sleep(1 / 120)
                        continue
                    # dragging a maximized window: restore its size under the cursor
                    _, _, width, height = self._restore_rect
                    ratio = (x0 - left) / max(1, right - left)
                    left = x0 - int(width * ratio)
                    right, bottom = left + width, top + height
                    self._restore_rect = None
                    self._round_corners(True)
                    self._push_state("normal")
                x1, y1, x2, y2 = left, top, right, bottom
                if edge is None:
                    x1, y1, x2, y2 = x1 + dx, y1 + dy, x2 + dx, y2 + dy
                else:
                    if "w" in edge:
                        x1 = min(left + dx, right - min_w)
                    if "e" in edge:
                        x2 = max(right + dx, left + min_w)
                    if "n" in edge:
                        y1 = min(top + dy, bottom - min_h)
                    if "s" in edge:
                        y2 = max(bottom + dy, top + min_h)
                if (x1, y1, x2, y2) != last:
                    user32.SetWindowPos(hwnd, None, x1, y1, x2 - x1, y2 - y1, flags)
                    last = (x1, y1, x2, y2)
                time.sleep(1 / 120)
        finally:
            self._tracking = False

    def _round_corners(self, rounded: bool) -> None:
        hwnd = self._hwnd()
        if hwnd is None:
            return
        import ctypes

        value = ctypes.c_int(_DWMWCP_ROUND if rounded else _DWMWCP_DONOTROUND)
        with contextlib.suppress(Exception):  # Windows 10 has no rounded corners
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(value), ctypes.sizeof(value)
            )

    def _push_current_state(self) -> None:
        self._push_state("maximized" if self._restore_rect is not None else "normal")

    def _push_state(self, state: str) -> None:
        """Tell the page about minimize/maximize: it pauses the avatar and squares corners."""
        if self._window is None:
            return
        with contextlib.suppress(Exception):
            self._window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('aion-window', {{detail: '{state}'}}))"
            )

    # -- window buttons -------------------------------------------------------------------

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def toggle_maximize(self) -> bool:
        """Fill the work area of the current monitor, or go back; returns "is maximized"."""
        hwnd = self._hwnd()
        if hwnd is None:
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        flags = _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED
        if self._restore_rect is not None:
            left, top, width, height = self._restore_rect
            self._restore_rect = None
            user32.SetWindowPos(hwnd, None, left, top, width, height, flags)
            self._round_corners(True)
            self._push_state("normal")
            return False

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        self._restore_rect = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        work = info.rcWork
        self._round_corners(False)
        user32.SetWindowPos(
            hwnd, None, work.left, work.top, work.right - work.left, work.bottom - work.top, flags
        )
        self._push_state("maximized")
        return True

    def navigate(self, url: str) -> None:
        if self._window is not None:
            self._window.load_url(url)

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
