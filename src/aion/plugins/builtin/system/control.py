"""OS control primitives. Windows via WinAPI (ctypes), Linux/macOS via standard tools."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

MediaAction = Literal["play_pause", "next", "previous", "stop"]
PowerAction = Literal["shutdown", "restart", "sleep", "cancel"]

_VK = {
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "play_pause": 0xB3,
}


class Unsupported(RuntimeError):
    pass


def _press(key: str, times: int = 1) -> None:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(times):
        user32.keybd_event(_VK[key], 0, 1, 0)  # KEYEVENTF_EXTENDEDKEY
        user32.keybd_event(_VK[key], 0, 3, 0)  # | KEYEVENTF_KEYUP
        if times > 1:
            time.sleep(0.005)


def _run(*cmd: str) -> None:
    if shutil.which(cmd[0]) is None:
        raise Unsupported(f"нет утилиты {cmd[0]}")
    subprocess.run(cmd, check=True, capture_output=True, timeout=10)


def _endpoint() -> Any:
    """Windows Core Audio volume of the default output device (exact, readable)."""
    import comtypes  # pyright: ignore[reportMissingTypeStubs]
    from pycaw.pycaw import AudioUtilities  # pyright: ignore[reportMissingTypeStubs]

    with contextlib.suppress(OSError):
        comtypes.CoInitialize()  # worker threads need COM too; harmless if already done
    speakers = AudioUtilities.GetSpeakers()
    if speakers is None:
        raise Unsupported("нет устройства вывода звука")
    return speakers.EndpointVolume  # pyright: ignore[reportUnknownMemberType]


def get_volume() -> int | None:
    """Current volume in percent (None where it can't be read)."""
    if sys.platform != "win32":
        return None
    return round(float(_endpoint().GetMasterVolumeLevelScalar()) * 100)


def change_volume(step_percent: int, up: bool) -> int | None:
    """Turn the volume up or down; returns the new level where it is known."""
    if sys.platform == "win32":
        current = get_volume() or 0
        return set_volume(current + step_percent if up else current - step_percent)
    sign = "+" if up else "-"
    if sys.platform == "darwin":
        _run(
            "osascript",
            "-e",
            "set volume output volume "
            f"(output volume of (get volume settings) {sign} {step_percent})",
        )
    else:
        _run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{step_percent}%")
    return None


def set_volume(percent: int) -> int:
    percent = max(0, min(100, percent))
    if sys.platform == "win32":
        endpoint = _endpoint()
        endpoint.SetMasterVolumeLevelScalar(percent / 100, None)
        if percent > 0:
            endpoint.SetMute(0, None)  # "громкость 30" on a muted PC should be heard
    elif sys.platform == "darwin":
        _run("osascript", "-e", f"set volume output volume {percent}")
    else:
        _run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%")
    return percent


def set_mute(muted: bool) -> None:
    if sys.platform == "win32":
        _endpoint().SetMute(int(muted), None)
    elif sys.platform == "darwin":
        _run("osascript", "-e", f"set volume output muted {str(muted).lower()}")
    else:
        _run("pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0")


def media(action: MediaAction) -> None:
    if sys.platform == "win32":
        _press(action)
    elif sys.platform.startswith("linux"):
        cmd = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}[
            action
        ]
        _run("playerctl", cmd)
    else:
        raise Unsupported("управление медиа на macOS пока не поддерживается")


def lock_screen() -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        _run("pmset", "displaysleepnow")
    else:
        _run("loginctl", "lock-session")


def screenshot(folder: Path | None = None) -> Path:
    from PIL import ImageGrab

    folder = folder or Path.home() / "Pictures" / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"aion-{datetime.now():%Y%m%d-%H%M%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    return path


def power(action: PowerAction) -> None:
    if sys.platform == "win32":
        commands = {
            "shutdown": ["shutdown", "/s", "/t", "10"],
            "restart": ["shutdown", "/r", "/t", "10"],
            "cancel": ["shutdown", "/a"],
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        }
    elif sys.platform == "darwin":
        commands = {
            "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
            "restart": ["osascript", "-e", 'tell app "System Events" to restart'],
            "sleep": ["pmset", "sleepnow"],
            "cancel": ["true"],
        }
    else:
        commands = {
            "shutdown": ["systemctl", "poweroff"],
            "restart": ["systemctl", "reboot"],
            "sleep": ["systemctl", "suspend"],
            "cancel": ["shutdown", "-c"],
        }
    _run(*commands[action])
