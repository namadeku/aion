"""Stage 7 plugins. OS side effects (launching apps, volume, clipboard, network) are mocked."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aion.app import Aion
from aion.config import ConfigStore
from aion.core import NullOutput
from tests.helpers import load_builtin_module, say

launcher = load_builtin_module("apps", "launcher")
currency = load_builtin_module("calc", "currency")


# -- apps ------------------------------------------------------------------------------


def test_resolve_known_targets() -> None:
    shortcuts = {"telegram desktop": Path("tg.lnk"), "visual studio code": Path("code.lnk")}
    aliases = launcher.parse_aliases("рабочий проект = C:\\work\\project\nкривая строка")
    resolve = launcher.resolve

    assert resolve("ютуб", aliases, shortcuts).location == "https://www.youtube.com"
    assert resolve("сайт github.com", aliases, shortcuts).location == "https://github.com"
    assert resolve("загрузки", aliases, shortcuts).kind == "folder"
    assert resolve("рабочий проект", aliases, shortcuts).location == "C:\\work\\project"
    tg = resolve("телеграм", aliases, shortcuts)
    assert tg is not None
    assert tg.location == "tg.lnk"
    assert resolve("абракадабра", aliases, shortcuts) is None


def test_translit() -> None:
    assert launcher.translit("телеграм") == "telegram"


@pytest.fixture
def opened(app: Aion, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    inst = app.plugins.instance("apps")
    assert inst is not None
    inst._shortcuts = {}  # pyright: ignore[reportAttributeAccessIssue] - ignore this PC's Start Menu
    module = __import__(type(inst).__module__, fromlist=["open_target"])
    monkeypatch.setattr(module, "open_target", calls.append)
    monkeypatch.setattr(module.webbrowser, "open", calls.append)
    monkeypatch.setattr(module, "list_windows", lambda: [])  # no real windows in tests
    return calls


async def test_open_command(app: Aion, opened: list[Any]) -> None:
    assert await say(app, "открой ютуб") == ["Открываю ютуб в браузере."]
    assert opened[0].location == "https://www.youtube.com"


async def test_unknown_app_declines(app: Aion, opened: list[Any]) -> None:
    seen: list[str] = []

    async def fallback(turn: Any) -> None:
        seen.append(turn.text)

    app.dialog.fallback = fallback
    await say(app, "открой мне тайну вселенной")
    assert opened == []
    assert seen == ["открой мне тайну вселенной"]


async def test_search(app: Aion, opened: list[Any]) -> None:
    assert await say(app, "найди в интернете рецепт борща") == ["Ищу: рецепт борща."]
    assert (
        opened[0]
        == "https://www.google.com/search?q=%D1%80%D0%B5%D1%86%D0%B5%D0%BF%D1%82+%D0%B1%D0%BE%D1%80%D1%89%D0%B0"
    )


# -- system ----------------------------------------------------------------------------


@pytest.fixture
def control_calls(app: Aion, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    inst = app.plugins.instance("system")
    assert inst is not None
    package = type(inst).__module__.rsplit(".", 1)[0]
    control = __import__(package + ".control", fromlist=["x"])

    def record(name: str, result: Any = None) -> Any:
        def call(*args: Any) -> Any:
            calls.append((name, args))
            return result(*args) if callable(result) else result

        return call

    monkeypatch.setattr(control, "set_volume", record("set_volume", lambda p: max(0, min(100, p))))
    step = record("change_volume", lambda s, up: 50 + (s if up else -s))  # from 50%
    monkeypatch.setattr(control, "change_volume", step)
    monkeypatch.setattr(control, "get_volume", record("get_volume", 42))
    for name in ("set_mute", "media", "lock_screen", "power"):
        monkeypatch.setattr(control, name, record(name))
    return calls


class FakeMedia:
    """The Windows media panel with one player."""

    def __init__(self) -> None:
        self.players: list[Any] = []
        self.calls: list[str] = []

    def player(self, status: str) -> Any:

        return SimpleNamespace(
            app_id="ru.yandex.desktop.music", status=status, track="Кино — Группа крови"
        )

    async def play(self, app: str | None = None) -> Any:
        self.calls.append(f"play {app}")
        if not self.players or (app and "яндекс" not in app):
            return None
        before = self.players[0]
        self.players = [self.player("playing")]
        return before

    async def pause(self) -> list[Any]:
        self.calls.append("pause")
        playing = [p for p in self.players if p.status == "playing"]
        self.players = [self.player("paused") for _ in self.players]
        return playing

    async def skip(self, forward: bool) -> Any:
        self.calls.append("next" if forward else "previous")
        return self.players[0] if self.players else None

    async def now_playing(self) -> Any:
        return self.players[0] if self.players else None


@pytest.fixture
def fake_media(app: Aion, monkeypatch: pytest.MonkeyPatch) -> FakeMedia:
    inst = app.plugins.instance("system")
    assert inst is not None
    media = __import__(type(inst).__module__.rsplit(".", 1)[0] + ".media", fromlist=["x"])
    fake = FakeMedia()
    for name in ("play", "pause", "skip", "now_playing"):
        monkeypatch.setattr(media, name, getattr(fake, name))
    return fake


async def test_volume(app: Aion, control_calls: list[Any]) -> None:
    assert await say(app, "сделай погромче") == ["60%."]
    assert await say(app, "громкость на тридцать процентов") == ["30%."]
    assert await say(app, "убавь звук до двадцати") == ["20%."]
    assert await say(app, "сделай тише на 15 процентов") == ["35%."]
    assert await say(app, "поменяй громкость на 40") == ["40%."]
    assert await say(app, "какая громкость") == ["Громкость 42%."]
    await say(app, "выключи звук")
    await say(app, "включи звук")
    assert control_calls == [
        ("change_volume", (10, True)),
        ("set_volume", (30,)),
        ("set_volume", (20,)),
        ("change_volume", (15, False)),
        ("set_volume", (40,)),
        ("get_volume", ()),
        ("set_mute", (True,)),
        ("set_mute", (False,)),
    ]


async def test_music_play_and_pause_are_not_a_toggle(
    app: Aion, control_calls: list[Any], fake_media: FakeMedia
) -> None:
    fake_media.players = [fake_media.player("paused")]
    assert await say(app, "включи музыку") == ["Включаю: Кино — Группа крови."]
    assert await say(app, "включи музыку") == ["Уже играет: Кино — Группа крови."]
    # a phrase the fuzzy match took for an app name: the open player plays
    assert await say(app, "поставь музыку на расслабиться") == ["Уже играет: Кино — Группа крови."]
    assert await say(app, "что сейчас играет") == ["Играет: Кино — Группа крови."]
    assert await say(app, "останови музыку") == []
    assert await say(app, "пауза") == ["Сейчас ничего не играет."]
    await say(app, "следующий трек")
    assert fake_media.calls[-1] == "next"
    assert control_calls == []  # no media keys: the player is controlled directly


async def test_music_in_app_opens_it(
    app: Aion, fake_media: FakeMedia, opened: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inst = app.plugins.instance("system")
    module = __import__(type(inst).__module__, fromlist=["x"])
    monkeypatch.setattr(module, "APP_START_S", 1.0)
    apps = app.plugins.instance("apps")
    assert apps is not None

    def resolve(name: str) -> Any:
        return launcher.Target("appid", name, "x") if "яндекс" in name else None

    monkeypatch.setattr(apps, "_resolve", resolve)

    async def appear() -> None:  # the app shows up in the media panel a bit later
        await asyncio.sleep(0.3)
        fake_media.players = [fake_media.player("stopped")]

    asyncio.get_running_loop().create_task(appear())
    replies = await say(app, "включи музыку в яндекс музыке")
    assert replies == ["Включаю: Кино — Группа крови."]
    assert [t.title for t in opened] == ["яндекс музыке"]
    assert await say(app, "включи музыку в спотифай") == ["Не нашёл приложение «спотифай»."]
    assert await say(app, "включи музыку") == ["Уже играет: Кино — Группа крови."]


async def test_music_without_any_player(app: Aion, fake_media: FakeMedia) -> None:
    (reply,) = await say(app, "включи музыку")
    assert reply.startswith("Не вижу открытого плеера")


async def test_shutdown_needs_confirmation(app: Aion, control_calls: list[Any]) -> None:
    app.dialog.ask_timeout = 0.2
    replies = await say(app, "выключи компьютер")
    assert "подтвердите" in replies[0]
    assert replies[-1] == "Отменено."
    assert control_calls == []


# -- clipboard -------------------------------------------------------------------------


async def test_clipboard_read(app: Aion, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = app.plugins.instance("clipboard")
    assert inst is not None
    module = __import__(type(inst).__module__, fromlist=["read_clipboard"])
    monkeypatch.setattr(module, "read_clipboard", lambda: "Привет из буфера")
    assert await say(app, "прочитай что скопировано") == ["Привет из буфера"]
    (reply,) = await say(app, "переведи скопированное")
    assert "языковая модель" in reply


# -- notes -----------------------------------------------------------------------------


async def test_notes_and_todos(app: Aion) -> None:
    assert await say(app, "запиши заметку купить хлеб") == ["Записал."]
    (notes,) = await say(app, "прочитай заметки")
    assert "купить хлеб" in notes
    await say(app, "добавь в список дел позвонить бабушке")
    await say(app, "добавь в список дел оплатить интернет")
    (todos,) = await say(app, "что у меня в списке дел")
    assert todos == "2 дела: позвонить бабушке; оплатить интернет."
    assert await say(app, "вычеркни оплатить интернет") == ["Вычеркнул: оплатить интернет."]
    (todos,) = await say(app, "что у меня в списке дел")
    assert todos == "1 дело: позвонить бабушке."


# -- currency --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("100 долларов в рублях", (100.0, "USD", "RUB")),
        ("50 евро в доллары", (50.0, "EUR", "USD")),
        ("10 фунтов стерлингов в рублях", (10.0, "GBP", "RUB")),
        ("рубль в юанях", (1.0, "RUB", "CNY")),
    ],
)
def test_parse_currency(text: str, expected: tuple[float, str, str]) -> None:
    req = currency.parse_currency(text)
    assert req is not None
    assert (req.amount, req.src, req.dst) == expected


async def test_currency_command(app: Aion, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = app.plugins.instance("calc")
    assert inst is not None

    async def fake_get() -> dict[str, float]:
        return {"RUB": 1.0, "USD": 80.0, "EUR": 90.0}

    monkeypatch.setattr(inst.rates, "get", fake_get)  # pyright: ignore[reportAttributeAccessIssue]
    assert await say(app, "сколько будет 100 долларов в рублях") == [
        "100 долларов — это примерно 8000 рублей."
    ]
    assert await say(app, "курс евро") == ["Курс ЦБ: 1 евро — 90 рублей."]


# -- home assistant --------------------------------------------------------------------


async def test_homeassistant_disabled_by_default(app: Aion) -> None:
    assert app.plugins.records["homeassistant"].status == "disabled"


async def test_homeassistant_switch(
    app_store: ConfigStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_store.update(
        {
            "plugins": {
                "enabled": ["homeassistant"],
                "settings": {"homeassistant": {"url": "http://ha", "token": "t"}},
            }
        },
        save=False,
    )
    aion = Aion(app_store, speech=lambda b, s, _c: NullOutput(b, s), watch_plugins=False)
    async with aion:
        inst = aion.plugins.instance("homeassistant")
        assert inst is not None
        requests: list[tuple[str, str, Any]] = []

        async def fake_request(method: str, path: str, json: Any = None) -> Any:
            requests.append((method, path, json))
            if path == "/api/states":
                return [
                    {
                        "entity_id": "light.kitchen",
                        "state": "off",
                        "attributes": {"friendly_name": "Свет на кухне"},
                    },
                    {
                        "entity_id": "switch.kettle",
                        "state": "off",
                        "attributes": {"friendly_name": "Чайник"},
                    },
                ]
            return []

        monkeypatch.setattr(inst, "_request", fake_request)
        assert await say(aion, "включи свет на кухне") == ["Включаю: Свет на кухне."]
        assert requests[-1] == (
            "POST",
            "/api/services/light/turn_on",
            {"entity_id": "light.kitchen"},
        )
        assert await say(aion, "выключи чайник") == ["Выключаю: Чайник."]
        assert requests[-1][1] == "/api/services/switch/turn_off"


# -- windows -----------------------------------------------------------------------------

windows_mod = load_builtin_module("apps", "windows")


def fake_windows() -> list[Any]:
    return [
        windows_mod.Window(1, "◐ Модели живости — Terminal", "windowsterminal"),
        windows_mod.Window(2, "Безымянный — Блокнот", "notepad"),
        windows_mod.Window(3, "Загрузки", "explorer"),
        windows_mod.Window(4, "Telegram", "telegram"),
    ]


def test_find_window_by_spoken_name(app: Aion) -> None:
    inst = app.plugins.instance("apps")  # main.py uses relative imports: take the loaded one
    module = __import__(type(inst).__module__, fromlist=["x"])
    windows = fake_windows()
    find = module.find_window
    assert find("блокнот", windows).process == "notepad"
    assert find("блокноте", windows).process == "notepad"  # case ending
    assert find("проводник", windows).process == "explorer"  # by program, not title
    assert find("телеграм", windows).process == "telegram"  # transliterated
    assert find("телегу", windows).process == "telegram"  # spoken nickname
    assert find("фотошоп", windows) is None
    # the name inside a longer phrase
    assert find("ка этот блокнот он мне больше не нужен", windows).process == "notepad"
    assert find("окно с телеграмом", windows).process == "telegram"
    assert find("он мне больше не нужен", windows) is None


@pytest.fixture
def window_calls(app: Aion, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    inst = app.plugins.instance("apps")
    assert inst is not None
    module = __import__(type(inst).__module__, fromlist=["x"])
    monkeypatch.setattr(module, "list_windows", fake_windows)
    monkeypatch.setattr(module, "close_window", lambda w: calls.append(f"close {w.title}"))
    monkeypatch.setattr(module, "minimize_window", lambda w: calls.append(f"min {w.title}"))
    monkeypatch.setattr(module, "focus_window", lambda w: calls.append(f"focus {w.title}") or True)
    monkeypatch.setattr(module, "minimize_all", lambda: calls.append("min all"))
    monkeypatch.setattr(module, "paste_text", lambda t: calls.append(f"type {t}"))
    return calls


async def test_window_commands(app: Aion, window_calls: list[str]) -> None:
    assert await say(app, "закрой блокнот") == ["Закрыл Безымянный — Блокнот."]
    assert await say(app, "сверни все окна") == ["Свернул все окна."]
    assert await say(app, "переключись на телеграм") == ["Переключаю на Telegram."]
    assert await say(app, "напиши в блокноте привет как дела") == [
        "Напечатал в Безымянный — Блокнот: привет как дела"
    ]
    assert window_calls == [
        "close Безымянный — Блокнот",
        "min all",
        "focus Telegram",
        "focus Безымянный — Блокнот",
        "type привет как дела",
    ]


async def test_closing_what_is_not_open_is_honest(app: Aion, window_calls: list[str]) -> None:
    assert await say(app, "закрой калькулятор") == ["Не вижу открытого окна «калькулятор»."]
    assert window_calls == []


def test_resolve_installed_store_apps() -> None:
    apps = {"telegram desktop": "TelegramMessengerLLP.TelegramDesktop_x!App", "yandex": "Yandex.Y"}
    tg = launcher.resolve("телеграм", {}, {}, apps)
    assert tg is not None
    assert tg.kind == "appid"
    assert tg.location == r"shell:AppsFolder\TelegramMessengerLLP.TelegramDesktop_x!App"
    browser = launcher.resolve("яндекс браузер", {}, {}, apps)
    assert browser is not None
    assert browser.title == "yandex"
    assert launcher.resolve("приложение телеграм", {}, {}, apps) is not None
