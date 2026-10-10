"""Desktop mascot behaviour: walking on the taskbar, sitting on windows, falling, being thrown.

Pure logic in screen pixels, independent of Win32, so it can be tested. The world (monitor
work areas and windows to sit on) comes from a :class:`World` implementation.

Coordinates: ``x`` is the body centre; ``y`` is the *anchor line* — the soles when standing,
walking, falling or dragged, the seat when sitting.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal, Protocol

Mode = Literal["stand", "walk", "sit", "drag", "fall", "peek"]

GRAVITY = 4.5  # frame heights per second squared
MAX_FALL = 6.0  # frame heights per second
WALK_SPEED = 0.3  # frame heights per second
HARD_LANDING = 1.2  # impact speed (frame heights / s) that plays the landing squat
EDGE_MARGIN = 0.12  # don't sit closer than this (frame heights) to a window's corner
PEEK_HIDE = 0.075  # body centre this far (frame heights) behind the screen edge when peeking
PEEK_CHANCE = 0.12  # share of idle decisions that go hide behind a screen edge


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def covers(self, other: Rect, tolerance: int = 8) -> bool:
        """Whether this rect spans all of ``other`` (give or take a few pixels)."""
        return (
            self.left <= other.left + tolerance
            and self.top <= other.top + tolerance
            and self.right >= other.right - tolerance
            and self.bottom >= other.bottom - tolerance
        )


@dataclass(frozen=True)
class Surface:
    """The top edge of a window the mascot may sit on."""

    id: int
    rect: Rect


class World(Protocol):
    def work_area(self, x: float, y: float) -> Rect:
        """Work area (screen minus taskbar) of the monitor nearest to the point."""
        ...

    def surfaces(self) -> list[Surface]:
        """Windows to sit on, topmost first."""
        ...

    def surface_rect(self, surface_id: int) -> Rect | None:
        """Current rect of a window, or None if it is gone, minimized or maximized."""
        ...

    def monitor_area(self, x: float, y: float) -> Rect:
        """Full bounds (taskbar included) of the monitor nearest to the point."""
        ...

    def edge_visible(self, surface: Surface, x: float) -> bool:
        """Whether the window's top edge at ``x`` is not covered by another window."""
        ...


@dataclass
class Layout:
    """Character position inside the frame, in pixels (reported by the renderer)."""

    width: int
    height: int
    feet: float
    seat: float
    center: float
    head: float

    @classmethod
    def default(cls, width: int, height: int) -> Layout:
        return cls(width, height, height * 0.97, height * 0.62, width / 2, height * 0.26)


class Physics:
    def __init__(self, layout: Layout, rng: random.Random | None = None) -> None:
        self.layout = layout
        self.rng = rng or random.Random()
        self.mode: Mode = "fall"
        self.x = 0.0
        self.y = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.facing = 0
        self.surface: int | None = None  # window we sit on; None = the taskbar edge
        self._offset = 0.0  # x relative to the window's left edge while sitting on it
        self._timer = 1.0
        self._settling = False  # easing onto the taskbar edge after sitting down / standing up
        # peeking: the screen edge she hides behind (-1 left, 1 right; 0 none), on her way
        # there, and stepping back out from behind it
        self.peek_side = 0
        self._to_peek = False
        self._emerging = False

    # -- geometry ---------------------------------------------------------------------

    @property
    def unit(self) -> float:
        """One frame height in pixels: speeds scale with the character size."""
        return float(self.layout.height)

    def anchor(self, mode: Mode | None = None) -> float:
        return self.layout.seat if (mode or self.mode) == "sit" else self.layout.feet

    def window_pos(self) -> tuple[int, int]:
        return round(self.x - self.layout.center), round(self.y - self.anchor())

    def place(self, left: float, top: float, mode: Mode) -> None:
        """Set the position from a window position (used while dragging)."""
        self.mode = mode
        self.x = left + self.layout.center
        self.y = top + self.anchor(mode)

    def _switch(self, mode: Mode) -> None:
        """Change mode keeping the window where it is (the anchor line moves)."""
        if mode == self.mode:
            return
        self.y += self.anchor(mode) - self.anchor()
        self.mode = mode

    def set_layout(self, layout: Layout) -> None:
        left, top = self.window_pos()
        self.layout = layout
        self.place(left, top, self.mode)

    def _half_width(self) -> float:
        return self.unit * 0.18

    # -- input ------------------------------------------------------------------------

    def drop(self, x: float, y: float) -> None:
        """Appear with the soles at (x, y) and fall from there."""
        self.mode, self.x, self.y = "fall", x, y
        self.vx = self.vy = 0.0
        self.surface = None
        self._stop_peeking()

    def grab(self) -> None:
        self._settling = False
        self._stop_peeking()
        self._switch("drag")
        self.surface = None
        self.facing = 0

    def release(self, vx: float, vy: float) -> None:
        limit = self.unit * 5
        self.vx = max(-limit, min(limit, vx))
        self.vy = max(-limit, min(limit, vy))
        self._switch("fall")

    # -- simulation -------------------------------------------------------------------

    def step(self, dt: float, world: World, *, busy: bool = False) -> list[str]:
        """Advance by ``dt`` seconds. Returns events: "land", "hard_land", "fall"."""
        if self.mode == "drag":
            return []
        if self.mode == "fall":
            return self._fall(dt, world)
        if self.mode == "sit" and self.surface is not None:
            return self._sit_on_window(world)
        if self.mode == "peek":
            return self._peek(dt, world, busy=busy)
        return self._on_ground(dt, world, busy=busy)

    # -- peeking from behind a screen edge ----------------------------------------------

    def _stop_peeking(self) -> None:
        self.peek_side = 0
        self._to_peek = self._emerging = False

    def _peek_edges(self, world: World, area: Rect) -> list[int]:
        """Screen edges she can hide behind: real monitor edges with no taskbar or monitor."""
        y = area.bottom - 1
        monitor = world.monitor_area(self.x, y)
        edges: list[int] = []
        if area.left == monitor.left and world.monitor_area(monitor.left - 1, y) == monitor:
            edges.append(-1)
        if area.right == monitor.right and world.monitor_area(monitor.right, y) == monitor:
            edges.append(1)
        return edges

    def _peek(self, dt: float, world: World, *, busy: bool) -> list[str]:
        area = world.work_area(self.x, self.y - 1)
        if self.y < area.bottom - 2:  # the taskbar moved or hid: fall to the new edge
            self._stop_peeking()
            self._switch("fall")
            return ["fall"]
        self.y = area.bottom
        side = self.peek_side
        edge = area.left if side < 0 else area.right
        self._timer -= dt
        if busy or self._timer <= 0:
            # step back out: talking needs room for the speech bubble
            self.mode, self.facing = "walk", -side
            self._emerging = True
            self.peek_side = 0
            self._timer = self.rng.uniform(2, 4)
            return []
        # sidle behind the edge until only a third of her is on screen
        target = edge + side * PEEK_HIDE * self.unit
        step = WALK_SPEED * self.unit * dt
        self.x = min(self.x + step, target) if side > 0 else max(self.x - step, target)
        return []

    def _fall(self, dt: float, world: World) -> list[str]:
        area = world.work_area(self.x, self.y - 1)
        self.vy = min(self.vy + GRAVITY * self.unit * dt, MAX_FALL * self.unit)
        self.vx *= max(0.0, 1 - dt * 0.4)
        prev = self.y
        self.y += self.vy * dt
        self.x += self.vx * dt
        half = self._half_width()
        if self.x < area.left + half or self.x > area.right - half:  # bounce off screen edges
            self.x = min(max(self.x, area.left + half), area.right - half)
            self.vx = -self.vx * 0.5
        impact = self.vy / self.unit
        if self.vy > 0:
            margin = self.unit * EDGE_MARGIN
            seat = self.layout.feet - self.layout.seat  # the seat is this much above the soles
            for surface in world.surfaces():
                r = surface.rect
                if (
                    prev - seat <= r.top <= self.y - seat
                    and r.left + margin <= self.x <= r.right - margin
                    and world.edge_visible(surface, self.x)
                ):
                    self.surface = surface.id
                    self._offset = self.x - r.left
                    self.mode = "sit"  # the seat lands on the edge: same line, new anchor
                    self.y = r.top
                    self.vx = self.vy = 0.0
                    self._timer = self.rng.uniform(20, 60)
                    return ["land"]
        if self.y >= area.bottom:
            self.y = area.bottom
            self.vx = self.vy = 0.0
            self.mode = "stand"
            self.surface = None
            self._timer = self.rng.uniform(1.5, 4)
            return ["hard_land" if impact > HARD_LANDING else "land"]
        if self.y < area.top - self.unit * 2:  # thrown far above the screen: come back down
            self.y = area.top - self.unit * 2
            self.vy = 0.0
        return []

    def _sit_on_window(self, world: World) -> list[str]:
        assert self.surface is not None
        rect = world.surface_rect(self.surface)
        margin = self.unit * EDGE_MARGIN
        if rect is not None:
            x = rect.left + self._offset
            surface = Surface(self.surface, rect)
            if rect.left + margin <= x <= rect.right - margin and world.edge_visible(surface, x):
                self.x, self.y = x, rect.top
                return []
        # the window moved away, closed, was minimized or covered: fall down
        self.surface = None
        self.vx = self.vy = 0.0
        self._switch("fall")
        return ["fall"]

    def _on_ground(self, dt: float, world: World, *, busy: bool) -> list[str]:
        area = world.work_area(self.x, self.y - 1)
        half = self._half_width()
        left, right = area.left + half, area.right - half
        if self._emerging:  # walking out from behind a screen edge: still partly off screen
            self._emerging = not left <= self.x <= right
        else:
            self.x = min(max(self.x, left), right)
        if self._settling:
            self.y += (area.bottom - self.y) * min(1.0, dt * 8)
            if abs(self.y - area.bottom) < 1:
                self._settling = False
        elif self.y < area.bottom - 2:  # the taskbar moved or hid: fall to the new edge
            self._switch("fall")
            return ["fall"]
        if not self._settling:
            self.y = area.bottom
        if busy and self.mode == "walk" and not self._emerging:  # stop and face the user
            self.mode, self.facing = "stand", 0
            self._to_peek = False
            self._timer = self.rng.uniform(3, 6)
        self._timer -= dt
        if self.mode == "walk":
            self._walk(dt, left, right)
        if self.mode != "peek" and self._timer <= 0 and not busy and not self._emerging:
            self._decide(world, area)
        return []

    def _walk(self, dt: float, left: float, right: float) -> None:
        self.x += self.facing * WALK_SPEED * self.unit * dt
        at_edge = self.x <= left if self.facing < 0 else self.x >= right
        if self._to_peek and at_edge:  # reached the screen edge: slip behind it
            self.x = min(max(self.x, left), right)
            self.mode, self.peek_side, self.facing = "peek", self.facing, -self.facing
            self._to_peek = False
            self._timer = self.rng.uniform(20, 50)
        elif not self._emerging and (self.x <= left or self.x >= right):
            self.facing = -self.facing

    def _decide(self, world: World, area: Rect) -> None:
        """Pick what to do next on the taskbar."""
        roll = self.rng.random()
        if self.mode == "sit":
            self._switch("stand")
            self._settling = True
            self._timer = self.rng.uniform(2, 5)
        elif roll < PEEK_CHANCE and (edges := self._peek_edges(world, area)):
            # go hide behind the nearer free screen edge
            self.mode = "walk"
            self.facing = min(edges, key=lambda e: abs((area.left, area.right)[e > 0] - self.x))
            self._to_peek = True
            self._timer = 60.0
        elif roll < 0.55:
            self.mode = "walk"
            self.facing = self.rng.choice((-1, 1))
            self._timer = self.rng.uniform(2, 6)
        elif roll < 0.75:
            self._switch("sit")  # sit on the taskbar edge, legs dangling
            self._settling = True
            self.facing = 0
            self._timer = self.rng.uniform(15, 40)
        else:
            self.mode, self.facing = "stand", 0
            self._timer = self.rng.uniform(4, 12)
