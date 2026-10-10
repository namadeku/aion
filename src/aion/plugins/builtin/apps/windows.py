"""Top-level windows (Windows): find by spoken name, close, minimize, focus, type into.

Pure ctypes. Other systems get empty lists, so commands decline instead of failing.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

WM_CLOSE = 0x0010
SW_RESTORE, SW_MINIMIZE = 9, 6
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004

# windows that are part of the shell, not apps
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    process: str  # exe name without extension, lowercase ("telegram", "explorer")


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _Input(ctypes.Structure):
    class _U(ctypes.Union):
        # the union must be as large as MOUSEINPUT, the biggest member
        _fields_ = [("ki", _KeyboardInput), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _user32() -> ctypes.WinDLL:
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    return user32


def _process_name(pid: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32")
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).stem.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _cloaked(hwnd: int) -> bool:
    """Hidden by DWM: suspended UWP apps, windows on other virtual desktops."""
    value = ctypes.c_int(0)
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
    )
    return bool(value.value)


def list_windows() -> list[Window]:
    """Visible app windows with a title, topmost first (as Alt+Tab shows them)."""
    if sys.platform != "win32":
        return []
    user32 = _user32()
    found: list[Window] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _cloaked(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in _SHELL_CLASSES:
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(Window(int(hwnd), title.value, _process_name(pid.value)))
        return True

    user32.EnumWindows(visit, 0)
    return found


def foreground() -> Window | None:
    if sys.platform != "win32":
        return None
    hwnd = _user32().GetForegroundWindow()
    return next((w for w in list_windows() if w.hwnd == hwnd), None)


def close_window(window: Window) -> bool:
    """Ask the window to close, as its × button does (the app may ask to save)."""
    return bool(_user32().PostMessageW(window.hwnd, WM_CLOSE, 0, 0))


def minimize_window(window: Window) -> bool:
    _user32().ShowWindow(window.hwnd, SW_MINIMIZE)
    return True


def focus_window(window: Window) -> bool:
    """Bring a window to the front (restoring it if minimized)."""
    user32 = _user32()
    if user32.IsIconic(window.hwnd):
        user32.ShowWindow(window.hwnd, SW_RESTORE)
    foreground = user32.GetForegroundWindow()
    if foreground == window.hwnd:
        return True
    # Windows lets only the foreground app hand focus over. Sharing its input queue for a
    # moment makes us part of it. (The popular trick of tapping Alt is worse: Notepad then
    # opens its menu, and typed letters become menu shortcuts.)
    kernel32 = ctypes.WinDLL("kernel32")
    ours = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True))
    try:
        user32.BringWindowToTop(window.hwnd)
        ok = bool(user32.SetForegroundWindow(window.hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)
    time.sleep(0.15)  # let the window take focus before keys are sent to it
    return ok and user32.GetForegroundWindow() == window.hwnd


def minimize_all() -> None:
    import comtypes.client  # pyright: ignore[reportMissingTypeStubs]

    comtypes.client.CreateObject("Shell.Application").MinimizeAll()  # pyright: ignore[reportUnknownMemberType]


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
VK_CONTROL, VK_V = 0x11, 0x56


def _clipboard_api() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    return user32, kernel32


def _open_clipboard(user32: ctypes.WinDLL) -> bool:
    for _ in range(10):  # another app may hold it for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def _get_clipboard_text() -> str | None:
    user32, kernel32 = _clipboard_api()
    if not _open_clipboard(user32):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer) if pointer else None
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_clipboard_text(text: str) -> bool:
    user32, kernel32 = _clipboard_api()
    data = ctypes.create_unicode_buffer(text)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(data))
    if not handle:
        return False
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, ctypes.sizeof(data))
    kernel32.GlobalUnlock(handle)
    if not _open_clipboard(user32):
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(CF_UNICODETEXT, handle))  # now owned by Windows
    finally:
        user32.CloseClipboard()


def paste_text(text: str) -> None:
    """Put text into the focused window through the clipboard (Ctrl+V), keeping the user's
    clipboard text. Reliable where simulated typing is not: Windows 11 Notepad garbles
    fast SendInput text after a dozen characters."""
    if sys.platform != "win32":
        raise OSError("вставка текста есть только в Windows")
    previous = _get_clipboard_text()
    if not _set_clipboard_text(text):
        raise OSError("буфер обмена занят другой программой")
    user32 = _user32()
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)  # the app reads the clipboard when it handles Ctrl+V
    if previous is not None:
        _set_clipboard_text(previous)


def type_text(text: str) -> None:
    """Type Unicode text into the focused window, as if from the keyboard."""
    if sys.platform != "win32":
        raise OSError("ввод текста есть только в Windows")
    user32 = _user32()
    events: list[_Input] = []
    for char in text.replace("\n", "\r"):
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            event = _Input(type=INPUT_KEYBOARD)
            event.ki = _KeyboardInput(0, ord(char), flags, 0, 0)
            events.append(event)
    array = (_Input * len(events))(*events)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(_Input))
    if sent != len(events):
        raise OSError("Windows не принял ввод (окно другого пользователя или администратора?)")
