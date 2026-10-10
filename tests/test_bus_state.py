from __future__ import annotations

from aion.core import EventBus, StateMachine
from aion.core.events import Event, StateChanged, WakeDetected


async def test_event_type_is_snake_case() -> None:
    assert WakeDetected.type == "wake_detected"
    msg = WakeDetected(word="аион").to_message()
    assert msg["type"] == "wake_detected"
    assert msg["word"] == "аион"


async def test_subscribe_by_class_string_and_wildcard(bus: EventBus) -> None:
    seen: list[str] = []

    async def async_handler(e: WakeDetected) -> None:
        seen.append(f"class:{e.word}")

    bus.subscribe(WakeDetected, async_handler)
    bus.subscribe("wake_detected", lambda e: seen.append("str"))
    bus.subscribe("*", lambda e: seen.append(f"any:{e.type}"))

    await bus.publish(WakeDetected(word="x"))
    assert sorted(seen) == ["any:wake_detected", "class:x", "str"]


async def test_failing_handler_does_not_break_others(bus: EventBus) -> None:
    seen: list[Event] = []

    def boom(_: Event) -> None:
        raise RuntimeError("boom")

    bus.subscribe(WakeDetected, boom)
    bus.subscribe(WakeDetected, seen.append)
    await bus.publish(WakeDetected(word="x"))
    assert len(seen) == 1


async def test_unsubscribe(bus: EventBus) -> None:
    seen: list[Event] = []
    unsubscribe = bus.subscribe(WakeDetected, seen.append)
    unsubscribe()
    await bus.publish(WakeDetected(word="x"))
    assert seen == []


async def test_state_priority_and_events(bus: EventBus, state: StateMachine) -> None:
    changes: list[tuple[str, str]] = []
    bus.subscribe(StateChanged, lambda e: changes.append((e.old, e.new)))

    state.raise_flag("thinking")
    state.raise_flag("speaking")
    assert state.state == "speaking"
    state.lower_flag("speaking")
    assert state.state == "thinking"
    with state.active("listening"):
        assert state.state == "listening"
    state.lower_flag("thinking")
    assert state.state == "idle"
    await bus.drain()
    assert changes == [
        ("idle", "thinking"),
        ("thinking", "speaking"),
        ("speaking", "thinking"),
        ("thinking", "listening"),
        ("listening", "thinking"),
        ("thinking", "idle"),
    ]


async def test_flags_are_counted(state: StateMachine) -> None:
    state.raise_flag("thinking")
    state.raise_flag("thinking")
    state.lower_flag("thinking")
    assert state.state == "thinking"
    state.lower_flag("thinking")
    state.lower_flag("thinking")  # extra lower is harmless
    assert state.state == "idle"
