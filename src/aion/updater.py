"""Updates of the installed (public) build from GitHub Releases.

A release carries ``Aion-Setup-<version>.exe`` built by Inno Setup. Updating = download it,
check its SHA-256 (GitHub's asset digest), run it silently with ``/RELAUNCH=1`` and quit:
the installer waits for Aion to exit, replaces the files and starts the new version.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from aion import __version__
from aion.edition import PUBLIC_REPO, is_installed, is_public
from aion.models import Progress, download_file

ASSET_RE = re.compile(r"^Aion-Setup-[\w.+-]+\.exe$")
INSTALLER_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1")


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str  # release page
    asset_url: str
    asset_name: str
    size: int
    sha256: str | None


def supported() -> bool:
    """Self-update works for the installed public build only (dev builds update via git)."""
    return is_public() and is_installed()


def parse_version(text: str) -> tuple[int, ...]:
    """``v1.2.10`` -> (1, 2, 10); anything after the numbers (``-rc1``) is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        raise ValueError(f"Не версия: {text!r}")
    return tuple(int(p) for p in match.group(1).split("."))


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def parse_release(data: dict[str, object]) -> Release | None:
    """The GitHub "latest release" JSON -> :class:`Release` (None without an installer)."""
    if data.get("draft") or data.get("prerelease"):
        return None
    assets = data.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or not ASSET_RE.match(str(asset.get("name", ""))):
            continue
        digest = str(asset.get("digest") or "")
        return Release(
            version=str(data.get("tag_name", "")).lstrip("v"),
            notes=str(data.get("body") or ""),
            url=str(data.get("html_url", "")),
            asset_url=str(asset["browser_download_url"]),
            asset_name=str(asset["name"]),
            size=int(asset.get("size") or 0),
            sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
        )
    return None


async def latest_release(repo: str = PUBLIC_REPO) -> Release | None:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"Aion/{__version__}"},
        )
        if r.status_code == 404:  # no releases yet
            return None
        r.raise_for_status()
        return parse_release(r.json())


async def check(repo: str = PUBLIC_REPO) -> Release | None:
    """The latest release if it is newer than the running version."""
    release = await latest_release(repo)
    if release is None or not is_newer(release.version):
        return None
    return release


async def download(release: Release, folder: Path, progress: Progress | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("Aion-Setup-*.exe"):  # installers of earlier updates
        if old.name != release.asset_name:
            old.unlink(missing_ok=True)
    target = folder / release.asset_name
    if not (target.exists() and _matches(target, release.sha256)):
        target.unlink(missing_ok=True)
        await download_file(release.asset_url, target, f"Aion {release.version}", progress)
    if release.sha256 is None:
        logger.warning("У релиза нет контрольной суммы — проверка пропущена")
    elif not _matches(target, release.sha256):
        target.unlink(missing_ok=True)
        raise ValueError("Контрольная сумма установщика не совпала — загрузка повреждена")
    return target


def launch_installer(installer: Path) -> None:
    """Start the installer detached; the caller must quit right after."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(installer), *INSTALLER_ARGS], creationflags=flags, close_fds=True)
    logger.info("Запущен установщик обновления {}", installer.name)


def _matches(path: Path, sha256: str | None) -> bool:
    if sha256 is None:
        return True
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest() == sha256.lower()
