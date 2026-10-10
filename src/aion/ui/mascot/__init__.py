"""Desktop mascot mode: the character leaves the window and lives on the Windows desktop.

* The avatar is rendered by an off-screen pywebview window (``?mode=desktop``) that streams
  RGBA frames over the UI WebSocket.
* Frames are shown by a native per-pixel transparent window (:mod:`aion.ui.mascot.win32`):
  empty pixels are click-through, the character catches the mouse.
* :mod:`aion.ui.mascot.physics` makes her walk along the taskbar, sit on window title bars,
  fall when a window goes away and fly when thrown.

Mouse: drag to carry and throw, click to poke, stroke the head to pat, double-click to go
back to the window, right-click for a menu.
"""

from __future__ import annotations

import contextlib
import math
import struct
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from loguru import logger

from aion.ui.mascot.physics import WALK_SPEED, Layout, Physics

Send = Callable[[dict[str, Any]], None]

TICK_MS = 16
FRAME_ASPECT = 0.62  # frame width / height
WORLD_REFRESH_S = 0.3
STATE_INTERVAL_S = 1 / 15
RENDER_TITLE = "Aion · desktop"
MENU_TALK, MENU_WINDOW, MENU_HOME = 1, 2, 3


def available() -> bool:
    return sys.platform == "win32"


class DesktopMascot:
    """Turns desktop mode on and off and runs the mascot thread while it is on."""

    def __init__(
        self,
        *,
        url: str,
        main_window: Any,
        send: Send,
        push_to_talk: Callable[[], None],
        scale: float = 0.42,
    ) -> None:
        self.url = url
        self.main_window = main_window  # aion.ui.desktop.Window
        self.send = send
        self.push_to_talk = push_to_talk
        self.scale = scale
        self.enabled = False
        self._lock = threading.Lock()
        self._toggle_lock = threading.Lock()
        self._frame: bytes | None = None
        self._layout: dict[str, float] | None = None
        self._hello = False
        self._busy = False
        self._renderer: Any = None
        self._thread: threading.Thread | None = None
        self._window: Any = None  # win32.LayeredWindow, owned by the mascot thread

    # -- called from other threads ----------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        with self._toggle_lock:
            if on == self.enabled or not available():
                return
            if on:
                self._start()
            else:
                self._stop()
            self.enabled = on
        self.send({"type": "desktop_mode", "value": on})
        logger.info("Режим рабочего стола {}", "включён" if on else "выключен")

    def toggle(self) -> None:
        self.set_enabled(not self.enabled)

    def on_message(self, message: dict[str, Any]) -> None:
        """WebSocket messages of the renderer page."""
        match message.get("type"):
            case "mascot_hello":
                with self._lock:
                    self._hello = True
            case "mascot_layout":
                with self._lock:
                    self._layout = {
                        k: float(message[k]) for k in ("feet", "seat", "center", "head")
                    }

    def on_frame(self, data: bytes) -> None:
        with self._lock:
            self._frame = data

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.set_enabled(False)

    # -- lifecycle --------------------------------------------------------------------

    def _start(self) -> None:
        import webview

        with self._lock:
            self._frame, self._layout, self._hello = None, None, False
        sep = "&" if "?" in self.url else "?"
        self._renderer = webview.create_window(
            RENDER_TITLE,
            f"{self.url}{sep}mode=desktop",
            width=520,
            height=860,
            frameless=True,
            focus=False,
            easy_drag=False,
            background_color="#000000",
        )
        self._renderer.events.shown += self._hide_renderer
        self._thread = threading.Thread(target=self._run, name="aion-mascot", daemon=True)
        self._thread.start()
        self.main_window.hide()

    def _stop(self) -> None:
        from aion.ui.mascot import win32

        window, thread = self._window, self._thread
        if window is not None:
            window.post(win32.WM_CLOSE)
        if thread is not None and thread is not threading.current_thread():
            thread.join(3)
        self._thread = None
        if self._renderer is not None:
            with contextlib.suppress(Exception):
                self._renderer.destroy()
            self._renderer = None
        self.main_window.show()

    def _hide_renderer(self) -> None:
        """Keep the renderer running but out of sight: off-screen, no taskbar button."""
        from aion.ui.mascot import win32

        native = getattr(self._renderer, "native", None)
        if native is None:
            return
        left, top = win32.virtual_screen_origin()
        win32.hide_from_taskbar(int(native.Handle.ToInt64()), left - 3000, top)

    # -- mascot thread ----------------------------------------------------------------

    def _run(self) -> None:
        from aion.ui.mascot import win32

        try:
            win32.make_thread_dpi_aware()
            loop = _MascotLoop(self)
            self._window = loop.window
            loop.run()
        except Exception:
            logger.exception("Персонаж на рабочем столе остановлен из-за ошибки")
            threading.Thread(target=self.set_enabled, args=(False,), daemon=True).start()
        finally:
            self._window = None


class _MascotLoop:
    """Everything that runs on the mascot thread: window messages, physics, frames."""

    def __init__(self, owner: DesktopMascot) -> None:
        from aion.ui.mascot import win32

        self.w32 = win32
        self.owner = owner
        self.world = win32.DesktopWorld()
        self.world.refresh()
        self.window = win32.LayeredWindow(self._on_message)
        self.size = (0, 0)
        cx, cy = win32.cursor_pos()
        area = self.world.work_area(cx, cy)
        width, height = self._frame_size(area.height)
        self.physics = Physics(Layout.default(width, height))
        # make an entrance: drop from the top of the screen, near the right side
        self.physics.drop(area.left + area.width * 0.8, area.top)
        self._announce_size(width, height)
        self.last = time.perf_counter()
        self.world_at = 0.0
        self.state_at = 0.0
        self.topmost_at = 0.0
        self.sent_state: dict[str, Any] = {}
        self.shown = False
        # mouse
        self.pressed = False
        self.dragging = False
        self.grab_offset = (0, 0)
        self.press_at = (0, 0)
        self.drag_velocity = (0.0, 0.0)
        self.last_cursor = win32.cursor_pos()
        self.pat_distance = 0.0
        self.pat_started = 0.0
        self.pat_cooldown = 0.0
        self.hover_x: int | None = None

    def _frame_size(self, area_height: int) -> tuple[int, int]:
        height = max(240, round(area_height * self.owner.scale))
        return round(height * FRAME_ASPECT), height

    def _announce_size(self, width: int, height: int) -> None:
        self.size = (width, height)
        self.owner.send({"type": "mascot_config", "width": width, "height": height})

    def run(self) -> None:
        self.window.start_timer(TICK_MS)
        self.w32.run_message_loop()

    # -- window messages --------------------------------------------------------------

    def _on_message(self, msg: int, wparam: int, lparam: int, hwnd: int) -> int | None:
        w = self.w32
        match msg:
            case w.WM_TIMER:
                self._tick()
                return 0
            case w.WM_MOUSEACTIVATE:
                return w.MA_NOACTIVATE
            case w.WM_LBUTTONDOWN:
                self._press()
                return 0
            case w.WM_LBUTTONUP:
                self._release()
                return 0
            case w.WM_CAPTURECHANGED:
                if self.pressed:
                    self._release()
                return 0
            case w.WM_MOUSEMOVE:
                self._hover(*w.mouse_point(lparam))
                return 0
            case w.WM_LBUTTONDBLCLK:
                self._detach(lambda: self.owner.set_enabled(False))
                return 0
            case w.WM_RBUTTONUP:
                self._menu()
                return 0
            case w.WM_CLOSE:
                self.window.destroy()
                return 0
            case w.WM_DESTROY:
                w.quit_message_loop()
                return 0
        return None

    def _detach(self, action: Callable[[], None]) -> None:
        """Run an action that stops this thread from another thread (no self-join)."""
        threading.Thread(target=action, daemon=True).start()

    def _press(self) -> None:
        cx, cy = self.w32.cursor_pos()
        left, top = self.physics.window_pos()
        self.pressed = True
        self.dragging = False
        self.press_at = (cx, cy)
        self.grab_offset = (cx - left, cy - top)
        self.drag_velocity = (0.0, 0.0)
        self.window.capture()

    def _release(self) -> None:
        if not self.pressed:
            return
        self.pressed = False
        self.window.release_capture()
        if self.dragging:
            self.dragging = False
            vx, vy = self.drag_velocity
            self.physics.release(vx, vy)
            return
        # a click without moving: poke, or a pat on the head
        _, y = self.press_at
        _, top = self.physics.window_pos()
        layout = self.physics.layout
        on_head = y - top < layout.head + layout.height * 0.16
        self._event("pat" if on_head else "poke")

    def _hover(self, x: int, y: int) -> None:
        """Stroking the head back and forth with the cursor counts as a pat."""
        if self.pressed:
            return
        layout = self.physics.layout
        now = time.perf_counter()
        on_head = (
            layout.head - layout.height * 0.02 <= y <= layout.head + layout.height * 0.16
            and abs(x - layout.center) < layout.height * 0.13
        )
        if not on_head or now < self.pat_cooldown:
            self.hover_x = None
            return
        if self.hover_x is None or now - self.pat_started > 1.5:
            self.pat_started, self.pat_distance = now, 0.0
        else:
            self.pat_distance += abs(x - self.hover_x)
        self.hover_x = x
        if self.pat_distance > layout.height * 0.3:
            self._event("pat")
            self.pat_cooldown = now + 2.5
            self.hover_x = None

    def _menu(self) -> None:
        x, y = self.w32.cursor_pos()
        choice = self.window.popup_menu(
            [
                (MENU_TALK, "Поговорить"),
                (MENU_WINDOW, "Открыть окно"),
                None,
                (MENU_HOME, "Вернуть в окно"),
            ],
            x,
            y,
        )
        if choice == MENU_TALK:
            self.owner.push_to_talk()
        elif choice == MENU_WINDOW:
            self.owner.main_window.show()
        elif choice == MENU_HOME:
            self._detach(lambda: self.owner.set_enabled(False))

    def _event(self, name: str) -> None:
        self.owner.send({"type": "mascot_event", "name": name})

    # -- per-frame work ---------------------------------------------------------------

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self.last)
        self.last = now
        owner = self.owner
        with owner._lock:  # pyright: ignore[reportPrivateUsage]
            frame, owner._frame = owner._frame, None  # pyright: ignore[reportPrivateUsage]
            layout = owner._layout  # pyright: ignore[reportPrivateUsage]
            owner._layout = None  # pyright: ignore[reportPrivateUsage]
            hello = owner._hello  # pyright: ignore[reportPrivateUsage]
            owner._hello = False  # pyright: ignore[reportPrivateUsage]
        if hello:  # the renderer (re)connected: tell it the frame size and pose
            self._announce_size(*self.size)
            self.sent_state = {}
        if layout is not None:
            w, h = self.size
            self.physics.set_layout(Layout(w, h, **layout))

        if now - self.world_at > WORLD_REFRESH_S:
            self.world_at = now
            self.world.refresh()

        cursor = self.w32.cursor_pos()
        if self.pressed:
            self._drag(cursor, dt)
        # nothing moves until the first frame is on screen (the model may still be loading)
        busy = owner._busy  # pyright: ignore[reportPrivateUsage]
        events = self.physics.step(dt, self.world, busy=busy) if self.shown else []
        if "hard_land" in events:
            self._event("land")
        self._resize_for_monitor()

        left, top = self.physics.window_pos()
        if frame is not None and (pixels := _decode(frame, self.size)) is not None:
            self.window.show_frame(pixels, left, top)
            if not self.shown:
                self.shown = True
                self.window.show()
                logger.info(
                    "Персонаж на экране: кадр {}x{}, заполнено {:.0%}, позиция {},{}",
                    *self.size,
                    float((pixels[..., 3] > 0).mean()),
                    left,
                    top,
                )
        else:
            self.window.move(left, top)
        if now - self.topmost_at > 1.0:  # stay above the taskbar and other topmost windows
            self.topmost_at = now
            self.window.raise_topmost()
        if now - self.state_at > STATE_INTERVAL_S:
            self.state_at = now
            self._send_state(cursor)
        self.last_cursor = cursor

    def _drag(self, cursor: tuple[int, int], dt: float) -> None:
        cx, cy = cursor
        if not self.dragging:
            px, py = self.press_at
            if math.hypot(cx - px, cy - py) < 6:
                return
            if not self.w32.left_button_down():
                return
            self.dragging = True
            self.physics.grab()
        gx, gy = self.grab_offset
        self.physics.place(cx - gx, cy - gy, "drag")
        if dt > 0:
            lx, ly = self.last_cursor
            vx, vy = (cx - lx) / dt, (cy - ly) / dt
            ox, oy = self.drag_velocity
            self.drag_velocity = (ox * 0.6 + vx * 0.4, oy * 0.6 + vy * 0.4)

    def _resize_for_monitor(self) -> None:
        """Keep the character size proportional to the monitor she is on."""
        area = self.world.work_area(self.physics.x, self.physics.y - 1)
        width, height = self._frame_size(area.height)
        if (width, height) != self.size and self.physics.mode != "drag":
            self.physics.set_layout(Layout.default(width, height))
            self._announce_size(width, height)

    def _velocity(self) -> tuple[float, float]:
        """How fast the character moves on screen, in pixels per second."""
        p = self.physics
        if p.mode == "drag":
            return self.drag_velocity
        if p.mode == "fall":
            return p.vx, p.vy
        if p.mode == "walk":
            return p.facing * WALK_SPEED * p.unit, 0.0
        return 0.0, 0.0

    def _send_state(self, cursor: tuple[int, int]) -> None:
        p = self.physics
        layout = p.layout
        left, top = p.window_pos()
        head_x = left + layout.center
        head_y = top + layout.head + layout.height * 0.07
        dx, dy = (cursor[0] - head_x) / layout.height, (head_y - cursor[1]) / layout.height
        look: list[float] | None = None
        if math.hypot(dx, dy) < 2.5:
            look = [
                round(max(-1.0, min(1.0, dx * 1.6)), 2),
                round(max(-1.0, min(1.0, dy * 1.6)), 2),
            ]
        vx, vy = self._velocity()
        state = {
            "type": "mascot_state",
            "mode": p.mode,
            "facing": p.facing,
            # in frame heights per second: the renderer swings the body, hair and clothes
            "vx": round(vx / layout.height, 2),
            "vy": round(vy / layout.height, 2),
            "look": look,
        }
        if state != self.sent_state:
            self.sent_state = state
            self.owner.send(state)


def _decode(data: bytes, size: tuple[int, int]) -> Any:
    """``[u32 width][u32 height][RGBA…]`` from the renderer → (h, w, 4) array, or None."""
    if len(data) < 8:
        return None
    width, height = struct.unpack_from("<II", data)
    if (width, height) != size or len(data) != 8 + width * height * 4:
        return None  # a frame of the previous size: skip until the renderer catches up
    return np.frombuffer(data, dtype=np.uint8, offset=8).reshape(height, width, 4)
