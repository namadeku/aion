"""Recording keys and mouse clicks into actions (the editor shows them, the user saves).

pynput listeners run in their own threads and call :meth:`MacroRecorder.on_press` etc.;
tests call those methods directly with fake key objects.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from aion.commands.keys import NAMED_KEYS

MODIFIERS = ("ctrl", "shift", "alt", "win")
MIN_WAIT_S = 0.3  # shorter pauses between inputs are not recorded
MAX_EVENTS = 2000
MAX_SECONDS = 600.0

_ALIASES = ("control", "escape", "return", "del", "cmd")  # the short name is used instead
_PYNPUT_TO_NAME = {v: k for k, v in NAMED_KEYS.items() if k not in _ALIASES}
_PYNPUT_TO_NAME |= {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "shift_l": "shift", "shift_r": "shift"}
_PYNPUT_TO_NAME |= {"alt_l": "alt", "alt_r": "alt", "alt_gr": "alt", "cmd_l": "win", "cmd_r": "win"}


def key_name(key: Any) -> str | None:
    """A pynput key -> our name (``"ctrl"``, ``"f5"``, ``"a"``), None for unknown keys."""
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return char.lower() if len(char) == 1 else None
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return _PYNPUT_TO_NAME.get(name, name if name in NAMED_KEYS else None)
    return None


class MacroRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[tuple[float, str, dict[str, Any]]] = []
        self._held: set[str] = set()
        self._listeners: list[Any] = []
        self._started = 0.0
        self.recording = False

    def start(self, *, listen: bool = True) -> None:
        """Begin a new recording; ``listen=False`` only resets (tests feed events by hand)."""
        self.stop_listening()
        with self._lock:
            self._events.clear()
            self._held.clear()
            self._started = time.monotonic()
            self.recording = True
        if listen:
            from pynput import keyboard, mouse

            self._listeners = [
                keyboard.Listener(on_press=self.on_press, on_release=self.on_release),
                mouse.Listener(on_click=self.on_click, on_scroll=self.on_scroll),
            ]
            for listener in self._listeners:
                listener.start()

    def stop(self) -> list[dict[str, Any]]:
        """End the recording and return it as command actions."""
        self.stop_listening()
        with self._lock:
            self.recording = False
            events = list(self._events)
            self._events.clear()
        return to_actions(events)

    def stop_listening(self) -> None:
        for listener in self._listeners:
            listener.stop()
        self._listeners = []

    # -- listener callbacks (other threads) ---------------------------------------------------

    def _add(self, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            if not self.recording:
                return
            now = time.monotonic()
            if len(self._events) >= MAX_EVENTS or now - self._started > MAX_SECONDS:
                self.recording = False  # a forgotten recording must not grow forever
                return
            self._events.append((now, kind, data))

    def on_press(self, key: Any) -> None:
        name = key_name(key)
        if name is None:
            return
        if name in MODIFIERS:
            with self._lock:
                self._held.add(name)
            return
        with self._lock:
            held = [m for m in MODIFIERS if m in self._held]
        if held and not (held == ["shift"] and len(name) == 1):
            self._add("combo", {"keys": [*held, name]})
        else:
            self._add("key", {"key": name, "char": getattr(key, "char", None)})

    def on_release(self, key: Any) -> None:
        name = key_name(key)
        if name in MODIFIERS:
            with self._lock:
                self._held.discard(name)

    def on_click(self, x: float, y: float, button: Any, pressed: bool) -> None:
        if pressed:
            which = getattr(button, "name", "left")
            self._add("click", {"x": int(x), "y": int(y), "button": which})

    def on_scroll(self, x: float, y: float, dx: float, dy: float) -> None:
        self._add("scroll", {"x": int(x), "y": int(y), "amount": int(dy)})


def to_actions(events: list[tuple[float, str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge raw input into readable actions: typed text, combos, clicks and pauses."""
    actions: list[dict[str, Any]] = []
    text: list[str] = []
    last: float | None = None

    def flush_text() -> None:
        if text:
            actions.append({"type": "keys", "text": "".join(text)})
            text.clear()

    for at, kind, data in events:
        if last is not None and at - last >= MIN_WAIT_S:
            flush_text()
            actions.append({"type": "wait", "seconds": round(min(at - last, 600.0), 1)})
        last = at
        char = data.get("char")
        if kind == "key" and isinstance(char, str) and len(char) == 1 and char.isprintable():
            text.append(char)
            continue
        if kind == "key" and data["key"] == "space":
            text.append(" ")
            continue
        flush_text()
        if kind == "key":
            actions.append({"type": "keys", "combo": data["key"]})
        elif kind == "combo":
            actions.append({"type": "keys", "combo": "+".join(data["keys"])})
        elif kind == "click":
            button = data.get("button", "left")
            action = "right" if button == "right" else "click"
            if (
                actions
                and actions[-1].get("type") == "mouse"
                and actions[-1].get("action") == "click"
                and action == "click"
                and (actions[-1]["x"], actions[-1]["y"]) == (data["x"], data["y"])
            ):
                actions[-1]["action"] = "double"
                continue
            actions.append({"type": "mouse", "action": action, "x": data["x"], "y": data["y"]})
        elif kind == "scroll" and data["amount"]:
            actions.append(
                {
                    "type": "mouse",
                    "action": "scroll",
                    "x": data["x"],
                    "y": data["y"],
                    "amount": data["amount"],
                }
            )
    flush_text()
    return actions
