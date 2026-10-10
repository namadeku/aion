"""Screenshot of the monitor the user is working on, scaled down for a vision model."""

from __future__ import annotations

import ctypes
import io
import sys
from ctypes import wintypes

from PIL import Image, ImageGrab

PER_MONITOR_AWARE_V2 = -4
MONITOR_DEFAULTTONEAREST = 2


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def active_monitor() -> tuple[int, int, int, int] | None:
    """Physical-pixel rectangle of the monitor with the foreground window (Windows)."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32")
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo)]
    # physical pixels, as the screenshot has them (this thread only)
    previous = user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(PER_MONITOR_AWARE_V2))
    try:
        monitor = user32.MonitorFromWindow(user32.GetForegroundWindow(), MONITOR_DEFAULTTONEAREST)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        r = info.rcMonitor
        return r.left, r.top, r.right, r.bottom
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)


def screenshot(max_side: int = 1600) -> bytes:
    """JPEG of the active monitor (or the whole screen), at most ``max_side`` pixels wide/high.

    Blocking: call it in a thread.
    """
    image = ImageGrab.grab(all_screens=True)
    if (rect := active_monitor()) is not None:
        # the virtual screen may start at negative coordinates (a monitor to the left)
        left, top = _virtual_origin()
        box = (rect[0] - left, rect[1] - top, rect[2] - left, rect[3] - top)
        if 0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height:
            image = image.crop(box)
    return encode(image, max_side)


def encode(image: Image.Image, max_side: int) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def _virtual_origin() -> tuple[int, int]:
    if sys.platform != "win32":
        return 0, 0
    user32 = ctypes.WinDLL("user32")
    user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    previous = user32.SetThreadDpiAwarenessContext(wintypes.HANDLE(PER_MONITOR_AWARE_V2))
    try:
        sm_xvirtualscreen, sm_yvirtualscreen = 76, 77
        return user32.GetSystemMetrics(sm_xvirtualscreen), user32.GetSystemMetrics(
            sm_yvirtualscreen
        )
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(previous)
