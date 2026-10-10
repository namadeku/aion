import random

from aion.ui.mascot.physics import Layout, Physics, Rect, Surface

SCREEN = Rect(0, 0, 1920, 1040)  # taskbar below 1040
MONITOR = Rect(0, 0, 1920, 1080)
LAYOUT = Layout(width=300, height=480, feet=470, seat=300, center=150, head=120)


class FakeWorld:
    def __init__(self, windows: dict[int, Rect] | None = None) -> None:
        self.windows = windows or {}
        self.covered: set[int] = set()
        self.monitors = [MONITOR]

    def work_area(self, x: float, y: float) -> Rect:
        return SCREEN

    def monitor_area(self, x: float, y: float) -> Rect:
        def distance(m: Rect) -> float:
            dx = max(m.left - x, 0, x - m.right + 1)
            dy = max(m.top - y, 0, y - m.bottom + 1)
            return dx + dy

        return min(self.monitors, key=distance)

    def surfaces(self) -> list[Surface]:
        return [Surface(i, r) for i, r in self.windows.items()]

    def surface_rect(self, surface_id: int) -> Rect | None:
        return self.windows.get(surface_id)

    def edge_visible(self, surface: Surface, x: float) -> bool:
        return surface.id not in self.covered


def run(physics: Physics, world: FakeWorld, seconds: float, *, busy: bool = False) -> list[str]:
    events: list[str] = []
    for _ in range(round(seconds * 60)):
        events += physics.step(1 / 60, world, busy=busy)
    return events


def test_falls_onto_the_taskbar_edge() -> None:
    p = Physics(LAYOUT, random.Random(1))
    p.drop(800, 0)
    events = run(p, FakeWorld(), 2)
    assert p.mode in {"stand", "walk", "sit"}
    assert events[0] in {"land", "hard_land"}
    _, top = p.window_pos()
    assert top + p.anchor() == SCREEN.bottom


def test_lands_on_a_window_and_sits_on_its_title_bar() -> None:
    world = FakeWorld({7: Rect(500, 400, 1300, 900)})
    p = Physics(LAYOUT, random.Random(1))
    p.drop(900, 0)
    run(p, world, 2)
    assert (p.mode, p.surface) == ("sit", 7)
    _, top = p.window_pos()
    assert top + LAYOUT.seat == 400  # the seat is on the window's top edge

    world.windows[7] = Rect(600, 300, 1400, 800)  # the window is moved: she rides along
    run(p, world, 0.1)
    assert p.x == 1000
    assert p.y == 300

    del world.windows[7]  # closed: she falls to the taskbar
    events = run(p, world, 2)
    assert "fall" in events
    assert p.surface is None
    assert p.y == SCREEN.bottom


def test_covered_window_edge_is_not_a_seat() -> None:
    world = FakeWorld({7: Rect(500, 400, 1300, 900)})
    world.covered.add(7)
    p = Physics(LAYOUT, random.Random(1))
    p.drop(900, 0)
    run(p, world, 2)
    assert p.surface is None
    assert p.y == SCREEN.bottom


def test_drag_and_throw() -> None:
    p = Physics(LAYOUT, random.Random(1))
    p.drop(800, SCREEN.bottom)
    run(p, FakeWorld(), 0.5)
    p.grab()
    assert p.mode == "drag"
    p.place(1000, 200, "drag")
    assert p.window_pos() == (1000, 200)
    p.release(3000, -500)
    assert p.mode == "fall"
    assert p.window_pos() == (1000, 200)
    run(p, FakeWorld(), 3)
    assert p.y == SCREEN.bottom
    assert SCREEN.left < p.x < SCREEN.right


def test_walks_but_stops_while_the_assistant_is_busy() -> None:
    p = Physics(LAYOUT, random.Random(3))
    p.drop(960, SCREEN.bottom)
    run(p, FakeWorld(), 60)
    seen = {p.mode}
    for _ in range(600):
        p.step(0.1, FakeWorld())
        seen.add(p.mode)
    assert "walk" in seen
    p.mode, p.facing = "walk", 1
    run(p, FakeWorld(), 0.1, busy=True)
    assert (p.mode, p.facing) == ("stand", 0)


def test_sitting_down_on_the_taskbar_does_not_jump() -> None:
    p = Physics(LAYOUT, random.Random(1))
    p.drop(960, SCREEN.bottom)
    run(p, FakeWorld(), 0.2)
    _, before = p.window_pos()
    p._switch("sit")  # pyright: ignore[reportPrivateUsage]
    p._settling = True  # pyright: ignore[reportPrivateUsage]
    p._timer = 100  # pyright: ignore[reportPrivateUsage]
    _, top = p.window_pos()
    assert top == before
    p.step(1 / 60, FakeWorld())
    _, step = p.window_pos()
    assert before < step < before + (LAYOUT.feet - LAYOUT.seat)  # eases down
    run(p, FakeWorld(), 2)
    assert p.y == SCREEN.bottom


def test_rect_covers() -> None:
    monitor = Rect(0, 0, 1920, 1080)
    assert Rect(0, 0, 1919, 1080).covers(monitor)  # NVIDIA overlay is 1 px short
    assert not Rect(0, 0, 1920, 1032).covers(monitor)  # a maximized window leaves the taskbar
    assert not Rect(615, 417, 1850, 1025).covers(monitor)


def test_renderer_gets_the_character_speed() -> None:
    from aion.ui.mascot import _MascotLoop  # pyright: ignore[reportPrivateUsage]

    loop = _MascotLoop.__new__(_MascotLoop)  # no Win32 window: only the speed logic
    loop.physics = Physics(LAYOUT, random.Random(1))
    loop.drag_velocity = (240.0, -48.0)
    loop.physics.mode = "drag"
    assert loop._velocity() == (240.0, -48.0)  # pyright: ignore[reportPrivateUsage]
    loop.physics.mode, loop.physics.facing = "walk", -1
    assert loop._velocity() == (-0.3 * 480, 0.0)  # pyright: ignore[reportPrivateUsage]
    loop.physics.mode, loop.physics.vx, loop.physics.vy = "fall", 10.0, 900.0
    assert loop._velocity() == (10.0, 900.0)  # pyright: ignore[reportPrivateUsage]
    loop.physics.mode = "sit"
    assert loop._velocity() == (0.0, 0.0)  # pyright: ignore[reportPrivateUsage]


def standing_at(x: float, world: FakeWorld, seed: int = 1) -> Physics:
    p = Physics(LAYOUT, random.Random(seed))
    p.drop(x, 0)
    run(p, world, 1.5)
    p.mode, p.facing, p.x = "stand", 0, x
    return p


def test_hides_behind_the_nearer_screen_edge_and_comes_back_out() -> None:
    world = FakeWorld()
    p = standing_at(300, world)
    p.rng.random = lambda: 0.0  # pyright: ignore[reportAttributeAccessIssue]  # always choose to peek
    p._timer = 0  # pyright: ignore[reportPrivateUsage]
    run(p, world, 0.1)
    assert (p.mode, p.facing) == ("walk", -1)  # heading to the left edge, the nearer one

    run(p, world, 6)
    assert (p.mode, p.peek_side, p.facing) == ("peek", -1, 1)  # facing back into the screen
    assert p.x == SCREEN.left - LAYOUT.height * 0.075  # half of her behind the edge

    run(p, world, 0.5, busy=True)  # the assistant talks: she steps out for the speech bubble
    assert (p.mode, p.facing) == ("walk", 1)
    run(p, world, 3, busy=True)
    assert p.x >= SCREEN.left + LAYOUT.height * 0.18  # fully on screen again
    assert p.mode == "stand"  # and stops to talk


def test_no_peeking_behind_a_taskbar_or_into_another_monitor() -> None:
    world = FakeWorld()
    p = standing_at(900, world)
    assert p._peek_edges(world, SCREEN) == [-1, 1]  # pyright: ignore[reportPrivateUsage]

    world.monitors.append(Rect(1920, 0, 3840, 1080))  # a second monitor on the right
    assert p._peek_edges(world, SCREEN) == [-1]  # pyright: ignore[reportPrivateUsage]

    taskbar_left = Rect(60, 0, 1920, 1080)  # a taskbar on the left side
    assert p._peek_edges(world, taskbar_left) == []  # pyright: ignore[reportPrivateUsage]


def test_grabbed_while_peeking() -> None:
    world = FakeWorld()
    p = standing_at(300, world)
    p.mode, p.peek_side, p.facing = "peek", -1, 1
    p.grab()
    assert (p.mode, p.peek_side) == ("drag", 0)
