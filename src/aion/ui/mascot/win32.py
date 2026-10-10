"""Win32 side of the desktop mascot: a per-pixel transparent window and the desktop "world".

WebView2 (pywebview) cannot draw a transparent window, so the character is rendered off-screen
and its RGBA frames are shown here with ``UpdateLayeredWindow``. A layered window with
per-pixel alpha is click-through wherever alpha is 0, so only the character itself catches
the mouse.

Every function here must run on the mascot thread (it is per-monitor DPI aware: all
coordinates are physical pixels).
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes as wt
from typing import Any

import numpy as np
import numpy.typing as npt

from aion.ui.mascot.physics import Rect, Surface

user32: Any = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
gdi32: Any = ctypes.WinDLL("gdi32")  # type: ignore[attr-defined]
dwmapi: Any = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]
kernel32: Any = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_APPWINDOW = 0x00040000
GWL_EXSTYLE = -20
CS_DBLCLKS = 0x0008

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CAPTURECHANGED = 0x0215
WM_APP = 0x8000
MA_NOACTIVATE = 3

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
IDC_HAND = 32649
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
VK_LBUTTON = 0x01
MONITOR_DEFAULTTONEAREST = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
ULW_ALPHA = 0x2
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

# Windows that are never surfaces nor cover anything we care about.
SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
    "NotifyIconOverflowWindow",
}


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
        ("hIconSm", wt.HICON),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


def _setup_signatures() -> None:  # noqa: PLR0915 - one line per Win32 function
    h, u, i, b = wt.HWND, wt.UINT, ctypes.c_int, wt.BOOL
    user32.DefWindowProcW.argtypes = [h, u, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, i, i, i, i, h, wt.HMENU, wt.HINSTANCE,
        wt.LPVOID,
    ]  # fmt: skip
    user32.CreateWindowExW.restype = h
    user32.PostMessageW.argtypes = [h, u, wt.WPARAM, wt.LPARAM]
    user32.SetTimer.argtypes = [h, ctypes.c_size_t, u, wt.LPVOID]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [h, ctypes.c_size_t]
    user32.UpdateLayeredWindow.argtypes = [
        h, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
        ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD,
    ]  # fmt: skip
    user32.UpdateLayeredWindow.restype = b
    user32.SetWindowPos.argtypes = [h, h, i, i, i, i, u]
    user32.GetDC.argtypes = [h]
    user32.GetDC.restype = wt.HDC
    user32.ReleaseDC.argtypes = [h, wt.HDC]
    user32.SetCapture.argtypes = [h]
    user32.SetCapture.restype = h
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPVOID]
    user32.LoadCursorW.restype = wt.HANDLE
    user32.SetCursor.argtypes = [wt.HANDLE]
    user32.TrackPopupMenu.argtypes = [wt.HMENU, u, i, i, i, h, wt.LPVOID]
    user32.AppendMenuW.argtypes = [wt.HMENU, u, ctypes.c_size_t, wt.LPCWSTR]
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.SetForegroundWindow.argtypes = [h]
    user32.DestroyWindow.argtypes = [h]
    user32.ShowWindow.argtypes = [h, i]
    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    for name in ("IsWindowVisible", "IsIconic", "IsZoomed", "IsWindow"):
        getattr(user32, name).argtypes = [h]
    user32.GetWindowThreadProcessId.argtypes = [h, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowTextLengthW.argtypes = [h]
    user32.GetClassNameW.argtypes = [h, wt.LPWSTR, i]
    user32.GetWindowLongW.argtypes = [h, i]
    user32.GetWindowLongW.restype = wt.LONG
    user32.SetWindowLongW.argtypes = [h, i, wt.LONG]
    user32.GetWindowRect.argtypes = [h, ctypes.POINTER(wt.RECT)]
    user32.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
    user32.MonitorFromPoint.restype = wt.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.SetThreadDpiAwarenessContext.argtypes = [wt.HANDLE]
    user32.SetThreadDpiAwarenessContext.restype = wt.HANDLE
    dwmapi.DwmGetWindowAttribute.argtypes = [h, wt.DWORD, wt.LPVOID, wt.DWORD]
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateDIBSection.argtypes = [
        wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), u, ctypes.POINTER(wt.LPVOID), wt.HANDLE,
        wt.DWORD,
    ]  # fmt: skip
    gdi32.CreateDIBSection.restype = wt.HBITMAP
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HMODULE


_setup_signatures()


def make_thread_dpi_aware() -> None:
    user32.SetThreadDpiAwarenessContext(wt.HANDLE(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def left_button_down() -> bool:
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def virtual_screen_origin() -> tuple[int, int]:
    return user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)


def hide_from_taskbar(hwnd: int, x: int, y: int) -> None:
    """Make a window a tool window (no taskbar button, no Alt+Tab) and move it to (x, y)."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.ShowWindow(hwnd, SW_HIDE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


def _rect(r: wt.RECT) -> Rect:
    return Rect(r.left, r.top, r.right, r.bottom)


def _window_rect(hwnd: int) -> Rect:
    """Visible bounds (without the invisible resize borders of Windows 10/11)."""
    r = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)
    ):
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _rect(r)


def _cloaked(hwnd: int) -> bool:
    value = wt.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _own_window(hwnd: int) -> bool:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


class DesktopWorld:
    """Monitors and top-level windows, refreshed on demand (``refresh``)."""

    def __init__(self) -> None:
        self._windows: list[tuple[int, Rect]] = []  # visible windows, topmost first
        self._surfaces: list[Surface] = []

    def refresh(self) -> None:
        found: list[int] = []

        def collect(hwnd: int, _: int) -> bool:
            found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(collect), 0)
        windows: list[tuple[int, Rect]] = []
        surfaces: list[Surface] = []
        for hwnd in found:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _own_window(hwnd):
                continue
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TRANSPARENT or _cloaked(hwnd):  # overlays that don't take clicks
                continue
            rect = _window_rect(hwnd)
            if rect.width <= 0 or rect.height <= 0 or _class_name(hwnd) in SHELL_CLASSES:
                continue
            if ex & WS_EX_TOOLWINDOW and rect.covers(self.monitor(rect)):
                continue  # full-screen overlays (NVIDIA, Steam…) draw nothing where she sits
            windows.append((hwnd, rect))
            if self._sittable(hwnd, ex, rect):
                surfaces.append(Surface(hwnd, rect))
        self._windows = windows
        self._surfaces = surfaces

    def _sittable(self, hwnd: int, ex: int, rect: Rect) -> bool:
        if ex & WS_EX_TOOLWINDOW or user32.IsZoomed(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return False
        if rect.width < 200 or rect.height < 120:
            return False
        area = self.work_area(rect.left + rect.width / 2, rect.top)
        return rect.top > area.top + 40  # fullscreen / at the very top: nowhere to sit

    # -- World protocol ---------------------------------------------------------------

    def work_area(self, x: float, y: float) -> Rect:
        monitor = user32.MonitorFromPoint(wt.POINT(round(x), round(y)), MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        return _rect(info.rcWork)

    def monitor_area(self, x: float, y: float) -> Rect:
        monitor = user32.MonitorFromPoint(wt.POINT(round(x), round(y)), MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        return _rect(info.rcMonitor)

    def monitor(self, rect: Rect) -> Rect:
        """Full bounds of the monitor under the rect's centre."""
        return self.monitor_area((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)

    def surfaces(self) -> list[Surface]:
        return self._surfaces

    def surface_rect(self, surface_id: int) -> Rect | None:
        hwnd = surface_id
        if (
            not user32.IsWindow(hwnd)
            or not user32.IsWindowVisible(hwnd)
            or user32.IsIconic(hwnd)
            or user32.IsZoomed(hwnd)
            or _cloaked(hwnd)
        ):
            return None
        return _window_rect(hwnd)

    def edge_visible(self, surface: Surface, x: float) -> bool:
        y = surface.rect.top + 3
        for hwnd, rect in self._windows:
            if hwnd == surface.id:
                return True
            if rect.contains(x, y):
                return False
        return True


class LayeredWindow:
    """A borderless, always-on-top, per-pixel transparent window that never takes focus."""

    CLASS_NAME = "AionMascot"

    def __init__(self, wndproc: Callable[[int, int, int, int], int | None]) -> None:
        self._handler = wndproc
        self._proc = WNDPROC(self._dispatch)  # keep a reference: called from C
        instance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = CS_DBLCLKS
        wc.lpfnWndProc = self._proc
        wc.hInstance = instance
        wc.hCursor = user32.LoadCursorW(None, wt.LPVOID(IDC_HAND))
        wc.lpszClassName = self.CLASS_NAME
        user32.RegisterClassExW(ctypes.byref(wc))  # fails harmlessly if already registered
        self.hwnd: int = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.CLASS_NAME,
            "Aion",
            WS_POPUP,
            0,
            0,
            1,
            1,
            None,
            None,
            instance,
            None,
        )
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        self._bitmap: int | None = None
        self._old_bitmap: int | None = None
        self._pixels: npt.NDArray[np.uint8] | None = None
        self.size = (0, 0)
        self.pos = (0, 0)

    def _dispatch(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            result = self._handler(msg, wparam, lparam, hwnd)
        except Exception:  # never let an exception unwind through C
            from loguru import logger

            logger.exception("Ошибка в окне персонажа")
            result = None
        if result is not None:
            return result
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _ensure_bitmap(self, width: int, height: int) -> npt.NDArray[np.uint8]:
        if self._pixels is not None and self.size == (width, height):
            return self._pixels
        self._free_bitmap()
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # top-down rows, like the canvas
        header.biPlanes = 1
        header.biBitCount = 32
        bits = wt.LPVOID()
        self._bitmap = gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0
        )
        if not self._bitmap or not bits.value:
            raise OSError("CreateDIBSection failed")
        self._old_bitmap = gdi32.SelectObject(self._mem_dc, self._bitmap)
        buffer = (ctypes.c_uint8 * (width * height * 4)).from_address(bits.value)
        self._pixels = np.ctypeslib.as_array(buffer).reshape(height, width, 4)
        self.size = (width, height)
        return self._pixels

    def show_frame(self, rgba: npt.NDArray[np.uint8], x: int, y: int) -> None:
        """Show straight-alpha RGBA pixels (from a canvas) at screen position (x, y)."""
        height, width = rgba.shape[:2]
        out = self._ensure_bitmap(width, height)
        alpha = rgba[..., 3:4].astype(np.uint16)
        # BGRA with premultiplied alpha, as UpdateLayeredWindow expects
        out[..., :3] = ((rgba[..., 2::-1].astype(np.uint16) * alpha + 127) // 255).astype(np.uint8)
        out[..., 3] = rgba[..., 3]
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        self.pos = (x, y)
        user32.UpdateLayeredWindow(
            self.hwnd,
            self._screen_dc,
            ctypes.byref(wt.POINT(x, y)),
            ctypes.byref(wt.SIZE(width, height)),
            self._mem_dc,
            ctypes.byref(wt.POINT(0, 0)),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )

    def move(self, x: int, y: int) -> None:
        if (x, y) == self.pos:
            return
        self.pos = (x, y)
        user32.SetWindowPos(self.hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def show(self) -> None:
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)

    def raise_topmost(self) -> None:
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        )

    def start_timer(self, interval_ms: int) -> None:
        user32.SetTimer(self.hwnd, 1, interval_ms, None)

    def capture(self) -> None:
        user32.SetCapture(self.hwnd)

    def release_capture(self) -> None:
        user32.ReleaseCapture()

    def popup_menu(self, items: list[tuple[int, str] | None], x: int, y: int) -> int:
        """Show a context menu; returns the chosen item id or 0."""
        menu = user32.CreatePopupMenu()
        try:
            for item in items:
                if item is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(menu, MF_STRING, item[0], item[1])
            user32.SetForegroundWindow(self.hwnd)  # otherwise the menu won't close on click-away
            return int(
                user32.TrackPopupMenu(
                    menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, self.hwnd, None
                )
            )
        finally:
            user32.DestroyMenu(menu)

    def post(self, msg: int) -> None:
        user32.PostMessageW(self.hwnd, msg, 0, 0)

    def _free_bitmap(self) -> None:
        if self._bitmap:
            gdi32.SelectObject(self._mem_dc, self._old_bitmap)
            gdi32.DeleteObject(self._bitmap)
        self._bitmap = None
        self._pixels = None

    def destroy(self) -> None:
        user32.KillTimer(self.hwnd, 1)
        self._free_bitmap()
        gdi32.DeleteDC(self._mem_dc)
        user32.ReleaseDC(None, self._screen_dc)
        user32.DestroyWindow(self.hwnd)


def run_message_loop() -> None:
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def quit_message_loop() -> None:
    user32.PostQuitMessage(0)


def mouse_point(lparam: int) -> tuple[int, int]:
    """Client coordinates from a mouse message's LPARAM (signed 16-bit each)."""
    x = ctypes.c_short(lparam & 0xFFFF).value
    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
    return x, y
