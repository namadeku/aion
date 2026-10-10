"""Helpers for game plugins: finding Steam games and installing Game State Integration configs.

Game State Integration (CS2, Dota 2) is Valve's official way for a game to push its state
to a local HTTP endpoint; it is what tournament overlays use and does not trip anti-cheat.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from loguru import logger

from aion.core.initiative import Observation
from aion.text.plural import fmt_count

_LIBRARY_PATH = re.compile(r'"path"\s+"(?P<path>[^"]+)"')


def steam_root() -> Path | None:
    if sys.platform != "win32":
        path = Path.home() / ".steam" / "steam"
        return path if path.is_dir() else None
    import winreg

    for hive, key in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                name = "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath"
                value, _ = winreg.QueryValueEx(handle, name)
        except OSError:
            continue
        path = Path(str(value))
        if path.is_dir():
            return path
    return None


def steam_libraries(root: Path | None = None) -> list[Path]:
    """All Steam library folders (games can live on several disks)."""
    root = root or steam_root()
    if root is None:
        return []
    libraries = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    for match in _LIBRARY_PATH.finditer(text):
        path = Path(match["path"].replace("\\\\", "\\"))
        if path not in libraries:
            libraries.append(path)
    return libraries


def find_steam_game(folder: str, libraries: list[Path] | None = None) -> Path | None:
    """Install folder of a game, e.g. ``find_steam_game("dota 2 beta")``."""
    for library in steam_libraries() if libraries is None else libraries:
        path = library / "steamapps" / "common" / folder
        if path.is_dir():
            return path
    return None


def gsi_config(name: str, port: int, token: str, data: list[str]) -> str:
    """Text of a ``gamestate_integration_*.cfg`` file (Valve KeyValues)."""
    fields = "\n".join(f'\t\t"{key}"\t"1"' for key in data)
    return (
        f'"{name}"\n{{\n'
        f'\t"uri"\t"http://127.0.0.1:{port}/"\n'
        '\t"timeout"\t"5.0"\n'
        '\t"buffer"\t"0.1"\n'
        '\t"throttle"\t"0.2"\n'
        '\t"heartbeat"\t"30.0"\n'
        f'\t"auth"\n\t{{\n\t\t"token"\t"{token}"\n\t}}\n'
        f'\t"data"\n\t{{\n{fields}\n\t}}\n'
        "}\n"
    )


def install_gsi_config(path: Path, text: str) -> bool:
    """Write the config if it is missing or outdated. Returns True if the file changed.

    The game reads it at startup, so a running game has to be restarted after a change.
    """
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    logger.info("Записан конфиг Game State Integration: {}", path)
    return True


ROUND_WON_QUICK = ["Раунд наш!", "Отлично, раунд взяли!"]
ROUND_LOST_QUICK = ["Ничего, отыграемся.", "Этот раунд не наш, следующий возьмём."]


class RoundStreak:
    """Rounds won (positive) or lost (negative) in a row, for remarks on round results."""

    def __init__(self) -> None:
        self.value = 0

    def reset(self) -> None:
        self.value = 0

    def record(self, *, won: bool, score: str) -> Observation:
        """Count a round and describe it; a streak or its end is more remarkable."""
        before = self.value
        self.value = max(before, 0) + 1 if won else min(before, 0) - 1
        team = "Команда пользователя"
        rounds = ("раунд", "раунда", "раундов")
        if won and before <= -3:
            text, importance = f"{team} наконец выиграла раунд после {-before} проигранных", 0.6
        elif won and self.value >= 4:
            text, importance = f"{team} выиграла {fmt_count(self.value, rounds)} подряд", 0.55
        elif won:
            text, importance = f"{team} выиграла раунд", 0.35
        elif self.value <= -3:
            text, importance = f"{team} проиграла {fmt_count(-self.value, rounds)} подряд", 0.55
        else:
            text, importance = f"{team} проиграла раунд", 0.3
        return Observation(
            f"{text}, {score}",
            importance=importance,
            key="round",
            quick=ROUND_WON_QUICK if won else ROUND_LOST_QUICK,
            emotion="joy" if won else "sad",
        )
