"""DesktopBackend wiring: every call into the OS is replaced by a recorder here."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aion.app import Aion
from aion.commands import platform
from aion.commands.platform import ActionError, DesktopBackend, builtin_module


class FakeTool:
    def __init__(self, name: str, reply: str, calls: list[Any]) -> None:
        self.qualified_name = name
        self.reply = reply
        self.calls = calls

    async def invoke(self, args: dict[str, Any], ctx: Any = None) -> str:
        self.calls.append((self.qualified_name, args))
        return self.reply


@pytest.fixture
def calls() -> list[Any]:
    return []


@pytest.fixture
def backend(app: Aion, calls: list[Any], monkeypatch: pytest.MonkeyPatch) -> DesktopBackend:
    """Apps tools answer from a table; nothing is opened, typed or clicked."""
    replies = {
        "apps__open_app": "Открыла Блокнот.",
        "apps__type_text_in": "Напечатала: привет",
        "apps__minimize_app": "Свернула Блокнот.",
        "apps__close_app": "Закрыла Блокнот.",
        "apps__switch_to_app": "Переключаю на Блокнот.",
    }
    tools = [FakeTool(name, reply, calls) for name, reply in replies.items()]
    monkeypatch.setattr(app.plugins, "tools", lambda: tools)
    monkeypatch.setattr(platform.webbrowser, "open", lambda url: calls.append(("browser", url)))
    monkeypatch.setattr(platform, "_maximize_foreground", lambda: calls.append(("maximize",)))
    monkeypatch.setattr(platform, "_press_combo", lambda keys: calls.append(("combo", keys)))
    monkeypatch.setattr(platform, "_mouse", lambda *a: calls.append(("mouse", *a)))
    monkeypatch.setattr(platform, "move_to_recycle_bin", lambda p: calls.append(("recycle", p)))
    monkeypatch.setattr(platform, "app_volume", lambda *a: calls.append(("app_volume", *a)) or 40)
    control = builtin_module("system", "control")
    monkeypatch.setattr(control, "set_volume", lambda p: calls.append(("set_volume", p)) or p)
    monkeypatch.setattr(
        control, "change_volume", lambda s, up: calls.append(("change", s, up)) or 55
    )
    monkeypatch.setattr(control, "set_mute", lambda m: calls.append(("mute", m)))
    monkeypatch.setattr(control, "power", lambda a: calls.append(("power", a)))
    monkeypatch.setattr(control, "lock_screen", lambda: calls.append(("lock",)))
    launcher = builtin_module("apps", "launcher")
    monkeypatch.setattr(
        launcher, "open_target", lambda t: calls.append(("open_target", t.location))
    )
    windows = builtin_module("apps", "windows")
    monkeypatch.setattr(windows, "minimize_all", lambda: calls.append(("minimize_all",)))
    return DesktopBackend(app)


def test_builtin_helpers_are_the_plugins_own_code() -> None:
    control = builtin_module("system", "control")
    assert builtin_module("system", "control") is control
    assert str(control.__file__).replace("\\", "/").endswith("plugins/builtin/system/control.py")
    assert callable(builtin_module("apps", "launcher").search_url)


async def test_open(backend: DesktopBackend, calls: list[Any], tmp_path: Path) -> None:
    assert await backend.open("https://example.com") == "Открываю https://example.com"
    assert await backend.open(str(tmp_path)) == f"Открываю {tmp_path}"
    assert await backend.open("блокнот") == "Открыла Блокнот."
    assert calls == [
        ("browser", "https://example.com"),
        ("open_target", str(tmp_path)),
        ("apps__open_app", {"name": "блокнот"}),
    ]


async def test_open_reports_what_apps_could_not_find(
    app: Aion, backend: DesktopBackend, calls: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app.plugins, "tools", lambda: [FakeTool("apps__open_app", "Не найдено: «xyz»", calls)]
    )
    with pytest.raises(ActionError, match="Не найдено"):
        await backend.open("xyz")
    monkeypatch.setattr(app.plugins, "tools", list)
    with pytest.raises(ActionError, match="выключен"):
        await backend.open("xyz")


async def test_search_uses_the_apps_engine_setting(
    app: Aion, backend: DesktopBackend, calls: list[Any]
) -> None:
    await backend.search("котики", None)
    await backend.search("котики", "yandex")
    assert calls[0][1].startswith("https://www.google.")
    assert "yandex" in calls[1][1]


async def test_windows_typing_keys_and_mouse(backend: DesktopBackend, calls: list[Any]) -> None:
    await backend.window("close", "блокнот")
    await backend.window("maximize", "блокнот")
    await backend.minimize_all()
    assert await backend.paste("привет") == "Напечатала: привет"
    await backend.keys(["ctrl", "c"])
    await backend.mouse("scroll", 1, 2, -3)
    assert calls == [
        ("apps__close_app", {"name": "блокнот"}),
        ("apps__switch_to_app", {"name": "блокнот"}),
        ("maximize",),
        ("minimize_all",),
        ("apps__type_text_in", {"text": "привет", "app": ""}),
        ("combo", ["ctrl", "c"]),
        ("mouse", "scroll", 1, 2, -3),
    ]


async def test_volume_and_power_go_through_the_system_plugin(
    backend: DesktopBackend, calls: list[Any]
) -> None:
    assert await backend.volume(set_to=30, change=None, mute=None, app=None) == "Громкость 30%"
    assert await backend.volume(set_to=None, change=-5, mute=None, app=None) == "Громкость 55%"
    assert await backend.volume(set_to=None, change=None, mute=True, app=None) == "Звук выключен"
    assert await backend.volume(set_to=None, change=10, mute=None, app="spotify") == "spotify: 40%"
    await backend.power("lock")
    await backend.power("shutdown")  # the recorder above, not the real thing
    assert calls == [
        ("set_volume", 30),
        ("change", 5, False),
        ("mute", True),
        ("app_volume", "spotify", None, 10, None),
        ("lock",),
        ("power", "shutdown"),
    ]


async def test_recycle(backend: DesktopBackend, calls: list[Any], tmp_path: Path) -> None:
    await backend.recycle(tmp_path / "x")
    assert calls == [("recycle", tmp_path / "x")]


async def test_play_through_the_assistants_player(
    app: Aion, backend: DesktopBackend, tmp_path: Path
) -> None:
    path = tmp_path / "beep.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(np.arange(1600) / 5) * 8000).astype(np.int16).tobytes())
    with pytest.raises(ActionError, match="голосовой вывод выключен"):
        await backend.play(path)  # text mode: no player

    played: list[tuple[int, int]] = []

    class Player:
        async def play(self, samples: Any, rate: int) -> None:
            played.append((len(samples), rate))

    app.speech.player = Player()  # type: ignore[attr-defined]
    await backend.play(path)
    assert len(played) == 1
    samples, rate = played[0]
    assert samples / rate == pytest.approx(0.1, abs=0.01)  # 1600 frames at 16 kHz
    with pytest.raises(ActionError, match="Нет файла"):
        await backend.play(tmp_path / "missing.wav")


def test_press_combo_releases_in_reverse(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[tuple[str, Any]] = []

    class Keyboard:
        def press(self, key: Any) -> None:
            log.append(("press", key))

        def release(self, key: Any) -> None:
            log.append(("release", key))

    import pynput.keyboard

    monkeypatch.setattr(pynput.keyboard, "Controller", Keyboard)
    platform._press_combo(["ctrl", "shift", "esc"])  # pyright: ignore[reportPrivateUsage]
    key = pynput.keyboard.Key
    assert log == [
        ("press", key.ctrl),
        ("press", key.shift),
        ("press", key.esc),
        ("release", key.esc),
        ("release", key.shift),
        ("release", key.ctrl),
    ]


@pytest.mark.parametrize(
    ("process", "app", "expected"),
    [
        ("Spotify.exe", "spotify", True),
        ("Discord.exe", "discord.exe", True),
        ("chrome.exe", "Chrome", True),
        ("msedge.exe", "chrome", False),
        ("spotify.exe", "", False),
    ],
)
def test_process_matching(process: str, app: str, expected: bool) -> None:
    assert platform._process_matches(process, app) is expected  # pyright: ignore[reportPrivateUsage]


def test_relative_paths_are_refused() -> None:
    with pytest.raises(ActionError, match="полный путь"):
        platform.expand_path("notes.txt")
    assert platform.expand_path("~/x.txt") == Path.home() / "x.txt"
