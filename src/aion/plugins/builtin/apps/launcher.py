"""Resolving spoken names to programs, websites and folders, and opening them."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote_plus

from rapidfuzz import fuzz, process

from aion.text import normalize

WINDOWS_APPS = {
    "блокнот": "notepad",
    "калькулятор": "calc",
    "проводник": "explorer",
    "диспетчер задач": "taskmgr",
    "панель управления": "control",
    "параметры": "ms-settings:",
    "настройки": "ms-settings:",
    "терминал": "wt",
    "командную строку": "cmd",
    "командная строка": "cmd",
    "paint": "mspaint",
    "пейнт": "mspaint",
    "ножницы": "snippingtool",
    "редактор реестра": "regedit",
}

WEBSITES = {
    "ютуб": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "гугл": "https://www.google.com",
    "яндекс": "https://ya.ru",
    "почту": "https://mail.google.com",
    "почта": "https://mail.google.com",
    "вконтакте": "https://vk.com",
    "вк": "https://vk.com",
    "github": "https://github.com",
    "гитхаб": "https://github.com",
    "википедию": "https://ru.wikipedia.org",
    "википедия": "https://ru.wikipedia.org",
    "карты": "https://yandex.ru/maps",
    "переводчик": "https://translate.yandex.ru",
    "кинопоиск": "https://www.kinopoisk.ru",
    "телеграм веб": "https://web.telegram.org",
}

FOLDERS = {
    "загрузки": "Downloads",
    "документы": "Documents",
    "рабочий стол": "Desktop",
    "изображения": "Pictures",
    "картинки": "Pictures",
    "музыку": "Music",
    "музыка": "Music",
    "видео": "Videos",
}

# How people say app names -> how Windows calls them (matched against installed apps).
SPOKEN_APPS = {
    "телеграм": "telegram", "телега": "telegram", "телегу": "telegram", "тг": "telegram",
    "дискорд": "discord", "стим": "steam", "эпик": "epic games launcher",
    "яндекс браузер": "yandex", "яндекс браузере": "yandex",
    "хром": "google chrome", "гугл хром": "google chrome", "эдж": "microsoft edge",
    "файрфокс": "firefox", "фаерфокс": "firefox", "опера": "opera",
    "ворд": "word", "эксель": "excel", "поверпоинт": "powerpoint", "аутлук": "outlook",
    "фотошоп": "photoshop", "вс код": "visual studio code", "вскод": "visual studio code",
    "обс": "obs studio", "спотифай": "spotify", "ватсап": "whatsapp", "вотсап": "whatsapp",
    "вайбер": "viber", "зум": "zoom", "скайп": "skype", "майнкрафт": "minecraft",
    "дота": "dota 2", "доту": "dota 2", "кс": "counter-strike 2", "контру": "counter-strike 2",
    "торрент": "qbittorrent", "вин рар": "winrar", "семизип": "7-zip",
}  # fmt: skip

# Words around the name that are not part of it: "открой приложение телеграм"
_FILLER = ("приложение ", "программу ", "программа ", "игру ", "мне ")

_DOMAIN = re.compile(r"(https?://)?[\w-]+(\.[\w-]+)*\.[a-zа-я]{2,}(/\S*)?")

SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}",
    "yandex": "https://yandex.ru/search/?text={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
    "bing": "https://www.bing.com/search?q={}",
}

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
        "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
        "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "sch", "ы": "y", "э": "e", "ю": "yu", "я": "ya", "ь": "", "ъ": "",
    }
)  # fmt: skip


def translit(text: str) -> str:
    return text.lower().translate(_TRANSLIT)


@dataclass(frozen=True)
class Target:
    kind: str  # "app" | "appid" | "shortcut" | "site" | "folder" | "alias"
    title: str
    location: str


def parse_aliases(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            name, _, target = line.partition("=")
            if name.strip() and target.strip():
                out[normalize(name, numbers=False)] = target.strip()
    return out


def installed_apps() -> dict[str, str]:
    """Display name (lowercase) -> AppID for every app in the Start Menu (Windows).

    Unlike .lnk files this includes Microsoft Store apps (Telegram from the Store has no
    shortcut). An AppID opens through ``shell:AppsFolder``.
    """
    if sys.platform != "win32":
        return {}
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout.decode("utf-8", errors="replace")
        items: Any = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if isinstance(items, dict):  # a single app comes as an object
        items = [items]
    found: dict[str, str] = {}
    for item in cast(list[dict[str, str]], items):
        name, app_id = str(item.get("Name", "")).strip(), str(item.get("AppID", ""))
        if not name or app_id.startswith(("http://", "https://")):
            continue  # "Steam Support Center" is a website
        if "uninstall" in name.lower() or "удален" in name.lower():
            continue
        found.setdefault(name.lower(), app_id)
    return found


def start_menu_shortcuts() -> dict[str, Path]:
    """Display name -> .lnk for every Start Menu shortcut (Windows)."""
    if sys.platform != "win32":
        return {}
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        / "Microsoft/Windows/Start Menu/Programs",
    ]
    found: dict[str, Path] = {}
    for root in roots:
        if root.is_dir():
            for lnk in root.rglob("*.lnk"):
                name = lnk.stem.lower()
                if "uninstall" in name or "удален" in name:
                    continue
                found.setdefault(name, lnk)
    return found


def resolve(
    spoken: str,
    aliases: dict[str, str],
    shortcuts: dict[str, Path],
    apps: dict[str, str] | None = None,
) -> Target | None:
    raw = spoken.lower().strip().removeprefix("сайт ").strip()
    if _DOMAIN.fullmatch(raw):  # "github.com" (normalization would eat the dot)
        return Target("site", raw, raw if raw.startswith("http") else f"https://{raw}")
    name = normalize(spoken, numbers=False).removeprefix("сайт ").removeprefix("папку ").strip()
    for filler in _FILLER:
        name = name.removeprefix(filler)
    if not name:
        return None
    for table, kind in ((aliases, "alias"), (WEBSITES, "site"), (FOLDERS, "folder")):
        if name in table:
            location = str(Path.home() / table[name]) if kind == "folder" else table[name]
            return Target(kind, name, location)
    if sys.platform == "win32" and name in WINDOWS_APPS:
        return Target("app", name, WINDOWS_APPS[name])
    # as spoken, as people call it in English ("телега" -> telegram), and transliterated
    queries = {name, SPOKEN_APPS.get(name, name), translit(name)}
    if apps and (hit := _best(queries, list(apps))):
        return Target("appid", hit, "shell:AppsFolder\\" + apps[hit])
    if shortcuts and (hit := _best(queries, list(shortcuts))):
        return Target("shortcut", hit, str(shortcuts[hit]))
    return None


def _best(queries: set[str], names: list[str]) -> str | None:
    best: tuple[str, float] | None = None
    for query in queries:
        hit = process.extractOne(query, names, scorer=fuzz.WRatio, score_cutoff=86)
        if hit and (best is None or hit[1] > best[1]):
            best = (hit[0], hit[1])
    return best[0] if best else None


def open_target(target: Target) -> None:
    location = target.location
    if target.kind == "site" or location.startswith(("http://", "https://")):
        webbrowser.open(location)
    elif sys.platform == "win32":
        if target.kind == "appid":  # Store and Start Menu apps by AppID
            subprocess.Popen(["explorer.exe", location])
        elif target.kind == "app" and not location.endswith(":"):
            subprocess.Popen(location, shell=False, creationflags=subprocess.DETACHED_PROCESS)
        else:
            os.startfile(location)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", location])
    else:
        subprocess.Popen(["xdg-open", location])


def search_url(engine: str, query: str) -> str:
    return SEARCH_URLS.get(engine, SEARCH_URLS["google"]).format(quote_plus(query))
