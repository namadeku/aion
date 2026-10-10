"""Assistant state machine: ``idle -> listening -> thinking -> speaking -> idle``.

Instead of letting every component push the state around (and race each other), the state
is *derived* from activity flags that components raise and lower:

* ``speaking``  — TTS is playing;
* ``listening`` — the mic is capturing a command or a reply is awaited;
* ``thinking``  — a dialog turn is being processed.

Priority is speaking > listening > thinking > idle, so e.g. a plugin asking a follow-up
question while its turn is running shows as ``listening``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from aion.core.bus import EventBus
from aion.core.events import State, StateChanged

Flag = Literal["speaking", "listening", "thinking"]
_PRIORITY: tuple[Flag, ...] = ("speaking", "listening", "thinking")


class StateMachine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._flags: Counter[Flag] = Counter()
        self._state: State = "idle"

    @property
    def state(self) -> State:
        return self._state

    def is_active(self, flag: Flag) -> bool:
        return self._flags[flag] > 0

    def raise_flag(self, flag: Flag) -> None:
        self._flags[flag] += 1
        self._recompute()

    def lower_flag(self, flag: Flag) -> None:
        if self._flags[flag] > 0:
            self._flags[flag] -= 1
        self._recompute()

    @contextmanager
    def active(self, flag: Flag) -> Iterator[None]:
        self.raise_flag(flag)
        try:
            yield
        finally:
            self.lower_flag(flag)

    def reset(self) -> None:
        self._flags.clear()
        self._recompute()

    def _recompute(self) -> None:
        new: State = next((f for f in _PRIORITY if self._flags[f] > 0), "idle")  # pyright: ignore[reportAssignmentType]
        if new != self._state:
            old, self._state = self._state, new
            self._bus.emit(StateChanged(old=old, new=new))
