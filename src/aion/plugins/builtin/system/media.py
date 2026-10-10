"""Players via the Windows media panel (System Media Transport Controls).

Unlike the play/pause media key, it tells which players are open, whether they are playing
and what, and can play or pause explicitly — so "включи музыку" never pauses it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from aion.text import normalize

# app id fragment -> how people call it (lowercase, normalized)
KNOWN_APPS: dict[str, tuple[str, ...]] = {
    "yandex": ("яндекс", "yandex"),
    "spotify": ("спотифай", "спотик", "spotify"),
    "vk": ("вк", "вконтакте", "vk"),
    "zunemusic": ("медиаплеер", "медиа плеер", "проигрыватель", "media player"),
    "applemusic": ("эпл мьюзик", "apple music", "айтюнс", "itunes"),
    "aimp": ("аимп", "aimp"),
    "foobar": ("фубар", "foobar"),
    "winamp": ("винамп", "winamp"),
    "chrome": ("хром", "chrome", "браузер", "ютуб", "youtube"),
    "msedge": ("эдж", "edge", "браузер", "ютуб", "youtube"),
    "firefox": ("файрфокс", "фаерфокс", "firefox", "браузер", "ютуб", "youtube"),
    "opera": ("опера", "opera", "браузер"),
    "telegram": ("телеграм", "telegram"),
}

# GlobalSystemMediaTransportControlsSessionPlaybackStatus
STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}


class MediaUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Player:
    app_id: str
    status: str
    artist: str = ""
    title: str = ""

    @property
    def track(self) -> str:
        return " — ".join(p for p in (self.artist, self.title) if p)


def _said(name: str, text: str, words: list[str]) -> bool:
    if " " in name:
        return name in text
    if len(name) <= 2:  # "вк" must not match inside "включи"
        return name in words
    return any(w.startswith(name) for w in words)  # "спотифае", "яндексе"


def matches(app_id: str, spoken: str) -> bool:
    """Does the player ``ru.yandex.desktop.music`` fit "яндекс музыке"?"""
    text = normalize(spoken, numbers=False)
    words = text.split()
    app = app_id.lower()
    return any(
        fragment in app and any(_said(name, text, words) for name in names)
        for fragment, names in KNOWN_APPS.items()
    )


def is_player_name(spoken: str) -> bool:
    """Does the phrase name a known player ("спотифай"), not just any word ("расслабиться")?"""
    text = normalize(spoken, numbers=False)
    words = text.split()
    return any(_said(name, text, words) for names in KNOWN_APPS.values() for name in names)


async def _manager() -> Any:
    if sys.platform != "win32":
        raise MediaUnavailable("управление плеерами есть только в Windows")
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
    except ImportError as e:
        raise MediaUnavailable("нет пакета winrt-Windows.Media.Control") from e
    return await Manager.request_async()


async def _describe(session: Any) -> Player:
    status = STATUS.get(int(session.get_playback_info().playback_status), "unknown")
    try:
        props = await session.try_get_media_properties_async()
        artist, title = str(props.artist or ""), str(props.title or "")
    except OSError:
        artist = title = ""
    return Player(str(session.source_app_user_model_id), status, artist, title)


async def _sessions() -> list[tuple[Any, Player]]:
    manager = await _manager()
    found = [(s, await _describe(s)) for s in manager.get_sessions()]
    current = manager.get_current_session()
    if current is not None:  # the one Windows considers active goes first
        found.sort(key=lambda item: item[1].app_id != str(current.source_app_user_model_id))
    return found


async def players() -> list[Player]:
    return [player for _, player in await _sessions()]


async def _pick(app: str | None) -> tuple[Any, Player] | None:
    sessions = await _sessions()
    if app:
        sessions = [item for item in sessions if matches(item[1].app_id, app)]
    playing = [item for item in sessions if item[1].status == "playing"]
    return (playing or sessions or [None])[0]


async def play(app: str | None = None) -> Player | None:
    """Resume a player (the named one, else the active one). None if there is no player."""
    picked = await _pick(app)
    if picked is None:
        return None
    session, player = picked
    if player.status != "playing":
        await session.try_play_async()
    return player


async def pause() -> list[Player]:
    """Pause everything that plays. Returns the paused players."""
    paused: list[Player] = []
    for session, player in await _sessions():
        if player.status == "playing":
            await session.try_pause_async()
            paused.append(player)
    return paused


async def skip(forward: bool) -> Player | None:
    picked = await _pick(None)
    if picked is None:
        return None
    session, player = picked
    await (session.try_skip_next_async() if forward else session.try_skip_previous_async())
    return player


async def now_playing() -> Player | None:
    picked = await _pick(None)
    return picked[1] if picked else None
