"""Key names for ``keys`` actions: parsing ``ctrl+shift+esc`` without importing pynput."""

from __future__ import annotations

#: modifier and named keys -> pynput ``Key`` attribute
NAMED_KEYS: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "win": "cmd",
    "cmd": "cmd",
    "esc": "esc",
    "escape": "esc",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "space": "space",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "capslock": "caps_lock",
    "printscreen": "print_screen",
    "menu": "menu",
    "pause": "pause",
    "volumeup": "media_volume_up",
    "volumedown": "media_volume_down",
    "volumemute": "media_volume_mute",
    "playpause": "media_play_pause",
    "nexttrack": "media_next",
    "prevtrack": "media_previous",
    **{f"f{n}": f"f{n}" for n in range(1, 21)},
}

MAX_COMBO_KEYS = 5


def parse_combo(combo: str) -> list[str]:
    """``"Ctrl+Shift+Esc"`` -> ``["ctrl", "shift", "esc"]``; raises ValueError on unknown keys.

    A key is a name from :data:`NAMED_KEYS` or one printable character (``ctrl+c``, ``alt+1``).
    """
    keys = [k.strip().lower() for k in combo.replace(" ", "").split("+")]
    if combo.strip().endswith("++"):  # "ctrl++" is Ctrl and the plus key
        keys = [*keys[:-2], "+"]
    if not keys or any(not k for k in keys):
        raise ValueError(f"Неверное сочетание клавиш «{combo}»")
    if len(keys) > MAX_COMBO_KEYS:
        raise ValueError(f"Слишком много клавиш в сочетании «{combo}»")
    for key in keys:
        if key not in NAMED_KEYS and len(key) != 1:
            raise ValueError(f"Неизвестная клавиша «{key}» в «{combo}»")
    return keys
