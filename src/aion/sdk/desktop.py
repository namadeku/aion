"""What the user is doing at the computer: idle time and full-screen apps (Windows).

On other systems the helpers return "unknown" (None / False) instead of failing.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


MONITOR_DEFAULTTONEAREST = 2
DESKTOP_CLASSES = {"Progman", "WorkerW"}  # the desktop itself is "full screen" too


def _user32() -> ctypes.WinDLL:
    # own instance: setting restype/argtypes must not affect other users of windll.user32
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    return user32


def user_idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input, None if unknown."""
    if sys.platform != "win32":
        return None
    info = _LastInputInfo(ctypes.sizeof(_LastInputInfo), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    # both counters are milliseconds since boot and wrap every ~49.7 days
    elapsed = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed / 1000


def fullscreen_app() -> bool:
    """A full-screen window (game, video, presentation) is in the foreground."""
    if sys.platform != "win32":
        return False
    user32 = _user32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    if name.value in DESKTOP_CLASSES:
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    screen = info.rcMonitor
    return (
        rect.left <= screen.left
        and rect.top <= screen.top
        and rect.right >= screen.right
        and rect.bottom >= screen.bottom
    )
